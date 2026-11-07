"""What's on the plate. Claude looks at the pass-camera photo, either through the API (ANTHROPIC_API_KEY) or through
the Claude Code CLI on this machine (its own login, no key). The QR code printed on each demo card is the fallback."""

import asyncio
import base64
import io
import json
import os
import re
import shutil
import tempfile

import anthropic
import zxingcpp
from PIL import Image, ImageOps

from .menu import BY_ID, MENU

MODEL = os.environ.get("HANDOFF_VISION_MODEL", "claude-sonnet-5-5")
# Talk to the API directly even when launched from a tool that sets its own ANTHROPIC_BASE_URL.
_client = anthropic.AsyncAnthropic(base_url=os.environ.get("HANDOFF_ANTHROPIC_BASE_URL", "https://api.anthropic.com")) \
    if os.environ.get("ANTHROPIC_API_KEY") else None
CLAUDE_CODE = shutil.which("claude") if os.environ.get("HANDOFF_CLAUDE_CODE", "1") == "1" else None
CLAUDE_CODE_MODEL = os.environ.get("HANDOFF_CLAUDE_CODE_MODEL", "claude-sonnet-5-5")  # ~2x faster than Opus here, same reads
_cc = {"model_flag": True}

SYSTEM = (
    "You are the expo check at a restaurant pass. A camera above the packing station photographs an order "
    "before it is bagged. The food may be real dishes or printed cards that each show one dish (a picture and a "
    "name); treat a printed card exactly like the dish on it. Count every dish you can see and map each one to "
    "the menu id it is. Count a card or dish once per copy (two burger cards = 2). Ignore hands, bags, the table "
    "and anything not on the menu, but list clearly visible food that is not on the menu under unrecognized. "
    "If a printed order ticket or bag label with an order number is readable, return that number as order_tag; "
    "otherwise null. Do not guess items you cannot see.\n\nMenu (id: name, what it looks like):\n"
    + "\n".join(f"- {m['id']}: {m['name']} {m['emoji']}, {m['look']}" for m in MENU)
)

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string", "enum": [m["id"] for m in MENU]}, "count": {"type": "integer"}},
            "required": ["id", "count"], "additionalProperties": False}},
        "unrecognized": {"type": "array", "items": {"type": "string"}},
        "order_tag": {"type": ["string", "null"]},
    },
    "required": ["items", "unrecognized", "order_tag"],
    "additionalProperties": False,
}


def vision_ready() -> bool:
    return _client is not None or CLAUDE_CODE is not None


def engine_label() -> str:
    return MODEL if _client else "Claude Code" if CLAUDE_CODE else "card codes"


def prepare(jpeg: bytes, side: int = 1024) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg))).convert("RGB")
    img.thumbnail((side, side))
    return img


def read_markers(img: Image.Image) -> tuple[dict, str | None]:
    """Decode the QR markers printed on the demo cards: HH:<menu id> per card, ORDER:<n> on bag labels."""
    best = ({}, None)
    # small codes in a wide shot often only decode once enlarged; keep whichever pass reads more cards
    for scale in (1, 2):
        im = img if scale == 1 else img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        found, tag = {}, None
        for b in zxingcpp.read_barcodes(im, formats=zxingcpp.BarcodeFormat.QRCode, binarizer=zxingcpp.Binarizer.GlobalHistogram):
            text = b.text.strip().upper()
            if text.startswith("HH:") and text[3:].lower() in BY_ID:
                found[text[3:].lower()] = found.get(text[3:].lower(), 0) + 1
            elif m := re.fullmatch(r"ORDER:(\d+)", text):
                tag = m.group(1)
        if sum(found.values()) + bool(tag) > sum(best[0].values()) + bool(best[1]):
            best = (found, tag)
    return best


async def ask_claude(img: Image.Image) -> dict:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    resp = await _client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": "What is on this order?"},
        ]}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("The model declined to read this photo.")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


async def ask_claude_code(img: Image.Image) -> dict:
    """One headless Claude Code turn: the photo goes in as an image block, the answer comes back against SCHEMA.
    Tools, settings, MCP servers and skills are all off, so it's a plain look-and-answer call on the CLI's login."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    msg = {"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(buf.getvalue()).decode()}},
        {"type": "text", "text": "What is on this order?"}]}}
    cmd = [CLAUDE_CODE, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--tools", "", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
           "--disable-slash-commands", "--system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA), "--effort", "low"]
    if _cc["model_flag"]:
        cmd += ["--model", CLAUDE_CODE_MODEL]
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, cwd=tempfile.gettempdir())
    try:
        out, err = await asyncio.wait_for(proc.communicate((json.dumps(msg) + "\n").encode()), timeout=90)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Claude Code took too long.")
    result = None
    for line in out.decode(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev
    if not result:
        raise RuntimeError("Claude Code gave no answer. " + err.decode(errors="replace")[-200:])
    text = str(result.get("result") or "")
    if result.get("is_error") or not result.get("structured_output"):
        if re.search(r"authenticat|log ?in|oauth|expired", text, re.I):
            raise RuntimeError("Claude Code is logged out. Run `claude` in Terminal and type /login.")
        if _cc["model_flag"] and re.search(r"model", text, re.I):  # older CLI or plan without this model
            _cc["model_flag"] = False
            return await ask_claude_code(img)
        raise RuntimeError("Claude Code: " + (text[:200] or "no structured answer"))
    return result["structured_output"]


async def identify(jpeg: bytes) -> dict:
    """Returns {items: {menu_id: count}, order_tag, unrecognized, engine}."""
    img = await asyncio.to_thread(prepare, jpeg, 2400)
    markers, marker_tag = await asyncio.to_thread(read_markers, img)
    small = img.copy()
    small.thumbnail((1280, 1280))
    engines = ([(ask_claude, MODEL)] if _client else []) + ([(ask_claude_code, "Claude Code")] if CLAUDE_CODE else [])
    out, warning = None, None
    for ask, label in engines:
        try:
            out = await ask(small)
            break
        except (anthropic.APIError, RuntimeError, json.JSONDecodeError, KeyError, OSError) as e:
            warning = str(e) if isinstance(e, RuntimeError) else f"{label} unavailable ({type(e).__name__})."
    if out is None:  # still give the pass an answer from the card codes
        return {"items": markers, "order_tag": marker_tag, "unrecognized": [], "engine": "card codes", "warning": warning}
    items = {}
    for it in out["items"]:
        if it["id"] in BY_ID and it["count"] > 0:
            items[it["id"]] = items.get(it["id"], 0) + it["count"]
    tag = marker_tag or (re.sub(r"\D", "", out["order_tag"] or "") or None)
    return {"items": items, "order_tag": tag, "unrecognized": out.get("unrecognized", []), "engine": label}
