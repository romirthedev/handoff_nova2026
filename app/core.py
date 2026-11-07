"""Kitchen state: tickets, cook timers, plate checks, and the voice-command parser. No I/O here."""

import re
import secrets
import time

from .menu import BY_ID, DEMO_ORDERS, MENU, cook_minutes

OPEN = ("new", "cooking", "ready", "issue", "verified")  # not picked up or cancelled
STATUS_LABEL = {"new": "New", "cooking": "Cooking", "ready": "Ready", "issue": "Issue",
                "verified": "Checked", "picked_up": "Picked up", "cancelled": "Cancelled"}


def now_ms() -> int:
    return int(time.time() * 1000)


def new_order(num, guest, source, items, courier=None, placed=None):
    return {
        "num": num, "guest": guest, "source": source, "courier": courier,
        "placed": placed or now_ms(), "status": "new", "check": None,
        "items": [{"uid": f"{num}-{i}", "menu": m, "qty": q, "mod": mod, "minutes": cook_minutes(m, mod),
                   "timer": None, "done": False} for i, (m, q, mod) in enumerate(items)],
    }


def demo_orders():
    t = now_ms()
    return {str(n): new_order(n, g, s, items, c, t - ago * 60_000) for n, g, s, ago, c, items in DEMO_ORDERS}


def new_kitchen(name: str, demo: bool = True):
    return {"id": secrets.token_urlsafe(5).replace("-", "x").replace("_", "y").lower(), "name": name.strip() or "My Kitchen",
            "created": now_ms(), "speed": 1, "orders": demo_orders() if demo else {}, "scans": [], "log": []}


def item_name(it) -> str:
    return BY_ID[it["menu"]]["name"] + (f" ({it['mod']})" if it["mod"] and BY_ID[it["menu"]]["cook"].__class__ is dict else "")


def remaining_s(it, t=None) -> float | None:
    if not it["timer"]:
        return None
    t = t or now_ms()
    return max(0.0, it["timer"]["secs"] - (t - it["timer"]["start"]) / 1000)


def fmt_left(secs: float) -> str:
    m, s = divmod(int(round(secs)), 60)
    if m and s:
        return f"{m} minute{'s' * (m != 1)} {s} seconds"
    return f"{m} minute{'s' * (m != 1)}" if m else f"{s} seconds"


def log(k, text):
    k["log"] = (k["log"] + [{"at": now_ms(), "text": text}])[-60:]


# ----------------------------------------------------------------------------- actions

def _refresh_status(o):
    """Cooking while any timer runs; Ready once every timed item is done. Manual states stay put."""
    if o["status"] in ("picked_up", "cancelled", "verified", "issue"):
        if any(it["timer"] and not it["done"] for it in o["items"]):
            o["status"] = "cooking"
        return
    running = any(it["timer"] and not it["done"] for it in o["items"])
    timed = [it for it in o["items"] if it["minutes"] or it["timer"]]
    if running:
        o["status"] = "cooking"
    elif timed and all(it["done"] for it in timed) and o["status"] == "cooking":
        o["status"] = "ready"


def start_timers(k, o, menu_id=None, minutes=None):
    """Start (or restart) timers on an order. Returns the items started and the ones that need no timer."""
    started, skipped = [], []
    for it in o["items"]:
        if menu_id and it["menu"] != menu_id:
            continue
        mins = minutes if minutes is not None else it["minutes"]
        if not mins:
            skipped.append(it)
            continue
        if not menu_id and minutes is None and it["timer"] and not it["done"]:
            continue  # "fire order 45" leaves running timers alone
        it["timer"] = {"start": now_ms(), "secs": mins * 60 / max(k.get("speed", 1), 1), "mins": mins}
        it["done"] = False
        started.append(it)
    _refresh_status(o)
    return started, skipped


def stop_timers(o, menu_id=None):
    stopped = []
    for it in o["items"]:
        if (not menu_id or it["menu"] == menu_id) and it["timer"]:
            it["timer"], it["done"] = None, False
            stopped.append(it)
    if o["status"] == "cooking" and not any(it["timer"] for it in o["items"]):
        o["status"] = "new"
    return stopped


def add_time(k, o, menu_id, minutes):
    changed = []
    for it in o["items"]:
        if (not menu_id or it["menu"] == menu_id) and it["timer"]:
            left = remaining_s(it)
            it["timer"] = {"start": now_ms(), "secs": left + minutes * 60 / max(k.get("speed", 1), 1),
                           "mins": it["timer"]["mins"] + minutes}
            it["done"] = False
            changed.append(it)
    _refresh_status(o)
    return changed


def mark_items_done(o, menu_id=None):
    for it in o["items"]:
        if not menu_id or it["menu"] == menu_id:
            if it["timer"]:
                it["timer"]["secs"] = (now_ms() - it["timer"]["start"]) / 1000
            it["done"] = True
    if not menu_id or all(it["done"] or not (it["minutes"] or it["timer"]) for it in o["items"]):
        if o["status"] in ("new", "cooking"):
            o["status"] = "ready"


def set_status(o, status):
    if status == "ready":
        mark_items_done(o)
    o["status"] = status


def tick(k, t=None):
    """Finish timers that ran out. Returns (order, item) pairs that just finished."""
    t = t or now_ms()
    finished = []
    for o in k["orders"].values():
        for it in o["items"]:
            if it["timer"] and not it["done"] and remaining_s(it, t) <= 0:
                it["done"] = True
                finished.append((o, it))
        if any(o is f[0] for f in finished):
            _refresh_status(o)
    return finished


# ----------------------------------------------------------------------------- plate check

PRIORITY = {"ready": 0, "issue": 1, "cooking": 2, "new": 3, "verified": 4}


def _expected(o):
    exp = {}
    for it in o["items"]:
        exp[it["menu"]] = exp.get(it["menu"], 0) + it["qty"]
    return exp


def _line(menu_id, qty):
    m = BY_ID[menu_id]
    return {"id": menu_id, "name": m["name"], "emoji": m["emoji"], "qty": qty}


def match_plate(k, detected: dict, order_tag=None):
    """Find the open order these items belong to and say what is missing or extra.

    detected maps menu id -> count. A readable order tag (a bag label) wins; otherwise the order whose
    items overlap most, ties going to the ticket furthest along, then the oldest.
    """
    detected = {m: c for m, c in detected.items() if c > 0 and m in BY_ID}
    open_orders = [o for o in k["orders"].values() if o["status"] in OPEN]
    tagged = k["orders"].get(str(order_tag)) if order_tag else None

    def score(o):
        exp = _expected(o)
        overlap = sum(min(detected.get(m, 0), q) for m, q in exp.items())
        missing = sum(max(q - detected.get(m, 0), 0) for m, q in exp.items())
        extra = sum(max(c - exp.get(m, 0), 0) for m, c in detected.items())
        return overlap, 2 * overlap - missing - extra

    ranked = sorted(((score(o), o) for o in open_orders),
                    key=lambda so: (-so[0][1], PRIORITY.get(so[1]["status"], 9), so[1]["placed"]))
    ranked = [so for so in ranked if so[0][0] > 0]

    if tagged:
        best, alts = tagged, []
    elif not detected:
        return {"ok": False, "order": None, "reason": "No food from the menu in view.", "present": [], "missing": [], "extra": []}
    elif not ranked:
        return {"ok": False, "order": None, "reason": "These items don't match any open ticket.",
                "present": [], "missing": [], "extra": [_line(m, c) for m, c in detected.items()]}
    else:
        best = ranked[0][1]
        alts = [o["num"] for s, o in ranked[1:] if s[1] == ranked[0][0][1]][:3]

    exp = _expected(best)
    present = [_line(m, min(detected.get(m, 0), q)) for m, q in exp.items() if detected.get(m, 0)]
    missing = [_line(m, q - detected.get(m, 0)) for m, q in exp.items() if q > detected.get(m, 0)]
    extra = [_line(m, c - exp.get(m, 0)) for m, c in detected.items() if c > exp.get(m, 0)]
    ok = not missing and not extra
    if ok:
        reason = "Everything on the ticket is here."
    else:
        parts = []
        if missing:
            parts.append("Missing " + ", ".join(f"{x['qty']}× {x['name']}" if x["qty"] > 1 else x["name"] for x in missing))
        if extra:
            parts.append("Extra " + ", ".join(f"{x['qty']}× {x['name']}" if x["qty"] > 1 else x["name"] for x in extra))
        reason = ". ".join(parts) + "."
    return {"ok": ok, "order": best["num"], "guest": best["guest"], "source": best["source"], "reason": reason,
            "present": present, "missing": missing, "extra": extra, "alternatives": alts,
            "by_tag": bool(tagged)}


def record_check(k, result):
    result = {**result, "id": secrets.token_hex(4), "at": now_ms()}
    o = k["orders"].get(str(result.get("order")))
    if o:
        if o["status"] in OPEN:
            o["status"] = "verified" if result["ok"] else "issue"
        o["check"] = {key: result[key] for key in ("ok", "reason", "present", "missing", "extra", "at", "id")}
    k["scans"] = ([result] + k["scans"])[:30]
    who = f"#{o['num']} {o['guest']}" if o else "Unmatched plate"
    log(k, f"{'✓' if result['ok'] else '✕'} {who}: {result['reason']}")
    return result


# ----------------------------------------------------------------------------- voice commands

_UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def words_to_digits(text: str) -> str:
    out, i, words = [], 0, text.split()
    while i < len(words):
        w = words[i]
        if w in _TENS:
            n = _TENS[w]
            if i + 1 < len(words) and words[i + 1] in _UNITS and 0 < _UNITS[words[i + 1]] < 10:
                n += _UNITS[words[i + 1]]
                i += 1
            out.append(str(n))
        elif w in _UNITS:
            out.append(str(_UNITS[w]))
        elif w in ("a", "an") and i + 1 < len(words) and words[i + 1].startswith(("minute", "min")):
            out.append("1")
        elif w == "half" and out and out[-1] in ("a", "and"):
            out.append("half")
        else:
            out.append(w)
        i += 1
    return " ".join(out)


_ALIASES = sorted(((a, m["id"]) for m in MENU for a in m["aliases"]), key=lambda x: -len(x[0]))
_INTENTS = [  # earlier wins when one order hears several
    ("picked_up", r"\b(picked up|pick(ed)? ?up|handed (it )?off|hand(ed)? off|handoff|out the door|delivered|collected)\b"),
    ("add", r"\b(add|plus|extend|another|more time)\b"),
    ("cancel", r"\b(cancel|cancelled|canceled|void|scratch)\b"),
    ("stop", r"\b(stop|pause|kill|reset|clear)\b"),
    ("query", r"\b(how long|time left|how much (time|longer)|status|where (are|is)|what'?s left|check on|eta)\b"),
    ("next", r"\b(what'?s next|next (order|ticket|up)|oldest)\b"),
    ("reopen", r"\b(reopen|re open|undo|back to new)\b"),
    ("start", r"\b(start|fire|begin|cook|drop|timer|put on|throw on|go on)\b"),
    ("show", r"\b(show|open|pull up|find|highlight)\b"),
    ("ready", r"\b(ready|done|finished|complete|bump|plated|up)\b"),
]
_RANK = {name: i for i, (name, _) in enumerate(_INTENTS)}
_ALL = r"\b(all|every|each|any|everything)\b"
_BULK = ("start", "stop", "add", "ready", "query")  # safe to run across the rail; cancel and hand-off need a number
_WAITING = ("new", "cooking", "ready", "issue")
_MINUTES = r"\b(\d+)(?: and a half| half)? ?(?:minutes?|mins?)\b|\bhalf (?:a|an) ?minute\b|\b30 seconds\b"


def _normalize(text: str) -> str:
    t = text.lower().replace("-", " ").replace("#", " number ")
    t = re.sub(r"[^\w\s']", " ", t)
    return words_to_digits(re.sub(r"\s+", " ", t).strip())


def _order_refs(t: str, k) -> list:
    """(start, end, num) wherever an order is named: "order 45", a bare open order number, or a guest's first name."""
    refs = []
    for m in re.finditer(r"\b(?:(?:order|ticket|number|table)\s+(?:number\s+)?)?(\d{1,4})\b(?!\s*(?:minutes?|mins?|seconds?|secs?)\b)", t):
        if m.group(0)[0].isalpha() or m.group(1) in k["orders"]:
            refs.append((m.start(), m.end(), m.group(1)))
    names = {}
    for o in k["orders"].values():
        if o["status"] in OPEN:
            names.setdefault(o["guest"].split()[0].lower(), []).append(str(o["num"]))
    for m in re.finditer(r"[a-z]+", t):
        if len(names.get(m.group(0), [])) == 1 and not any(a <= m.start() < b for a, b, _ in refs):
            refs.append((m.start(), m.end(), names[m.group(0)][0]))
    refs.sort()
    merged = []  # "order 45 ... 45" is one mention
    for r in refs:
        if merged and merged[-1][2] == r[2]:
            merged[-1] = (merged[-1][0], r[1], r[2])
        else:
            merged.append(r)
    return merged


def _nearest(refs, a, b, prefer=None):
    """Index of the order mention closest to the words at [a, b); ties go to the earlier order."""
    best = None
    for i, (ra, rb, num) in enumerate(refs):
        if prefer is not None and not prefer(num):
            continue
        gap = a - rb if a >= rb else (ra - b if b <= ra else 0)
        if best is None or gap < best[0]:
            best = (gap, i)
    return None if best is None else best[1]


def parse_commands(text: str, k) -> list[dict]:
    """Turn what someone said at the pass into one command per order mentioned.

    "Start the timer for order 41 smashburger, order 43 margherita pizza, and order 46 we can cancel that" gives
    three commands. Each dish, verb and time goes to the order named closest to it; an order with no verb of its
    own takes the one before it.
    """
    t = _normalize(text)
    refs = _order_refs(t, k)
    slots = [{"intents": set(), "items": [], "minutes": None} for _ in refs] or [{"intents": set(), "items": [], "minutes": None}]
    pick = (lambda a, b, prefer=None: _nearest(refs, a, b, prefer) or 0) if refs else (lambda a, b, prefer=None: 0)

    for m in re.finditer(_MINUTES, t):
        mins = int(m.group(1)) + (0.5 if "half" in m.group(0) else 0) if m.group(1) else 0.5
        slots[pick(m.start(), m.end())]["minutes"] = mins
    taken = []
    for alias, mid in _ALIASES:
        for m in re.finditer(rf"\b{re.escape(alias)}\b", t):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            has = lambda num, mid=mid: num in k["orders"] and any(it["menu"] == mid for it in k["orders"][num]["items"])
            i = (_nearest(refs, m.start(), m.end(), has) if refs else None)
            i = pick(m.start(), m.end()) if i is None else i
            slots[i]["items"].append((m.start(), mid))
    for name, rx in _INTENTS:
        for m in re.finditer(rx, t):
            slots[pick(m.start(), m.end())]["intents"].add(name)

    cmds, prev = [], None
    for i, slot in enumerate(slots):
        intents = slot["intents"]
        items = [mid for _, mid in sorted(slot["items"])]
        if "cancel" in intents:  # "cancel the timer" / "cancel the steak" stop timers; "cancel order 46" voids it
            intents = (intents - {"cancel", "start"}) | ({"stop"} if items or "start" in intents else {"cancel"})
        if "add" in intents and slot["minutes"] is None:
            intents.discard("add")
        intent = min(intents, key=_RANK.get) if intents else prev
        if intent == "next" and refs:
            intent = prev
        prev = intent or prev
        cmds.append({"intent": intent, "order": refs[i][2] if refs else None, "item": items[0] if items else None,
                     "items": items, "minutes": slot["minutes"], "text": text})
    if not refs and re.search(_ALL, t):
        return _for_every_order(cmds[0], k)
    return cmds


def _for_every_order(cmd, k) -> list[dict]:
    """"For all orders with garlic bread start the garlic bread timer": one command per open order that has the dish
    (or every open order when no dish is named)."""
    if cmd["intent"] not in _BULK:
        return [{**cmd, "all": True}]
    wanted = list(dict.fromkeys(cmd["items"]))
    out = []
    for o in sorted(k["orders"].values(), key=lambda o: o["placed"]):
        if o["status"] not in _WAITING:
            continue
        has = [mid for mid in wanted if any(it["menu"] == mid for it in o["items"])]
        if wanted and not has:
            continue
        out.append({**cmd, "order": str(o["num"]), "item": has[0] if has else None, "items": has, "all": True})
    return out or [{**cmd, "all": True}]


def parse_command(text: str, k) -> dict:
    return parse_commands(text, k)[0]


def run_commands(k, cmds) -> dict:
    """Run every command from one utterance and fold the replies into one."""
    results = []
    for c in cmds:
        items = c.get("items") or [c.get("item")]
        for mid in dict.fromkeys(items):  # "fire 45 steak and lobster" runs once per dish
            results.append(run_command(k, {**c, "item": mid}))
    ok = [r for r in results if r["ok"]]
    return {"ok": bool(ok), "say": " ".join(r["say"] for r in results),
            "order": (ok or results)[-1].get("order"), "results": results}


def run_command(k, cmd) -> dict:
    """Apply a parsed command. Returns {ok, say, order} where say is read aloud at the pass."""
    intent, num, mid, minutes = cmd["intent"], cmd["order"], cmd["item"], cmd["minutes"]
    if intent == "next":
        waiting = sorted((o for o in k["orders"].values() if o["status"] in ("new", "cooking", "ready", "issue")),
                         key=lambda o: o["placed"])
        if not waiting:
            return {"ok": True, "say": "No open tickets. Nice work."}
        o = waiting[0]
        return {"ok": True, "order": o["num"], "say": f"Oldest open ticket is order {o['num']} for {o['guest']}, "
                f"{STATUS_LABEL[o['status']].lower()}."}
    if not intent:
        return {"ok": False, "say": "I didn't catch a command. Try \"start the timer for order 45 steak\"."}
    if num is None and cmd.get("all"):
        if intent not in _BULK:
            return {"ok": False, "say": "I can only do that one order at a time. Say the order number."}
        what = " or ".join(BY_ID[m]["name"].lower() for m in cmd.get("items") or []) or "anything"
        return {"ok": False, "say": f"No open order has {what}."}
    if num is None or num not in k["orders"]:
        return {"ok": False, "say": f"Which order? I didn't hear an order number{f' ({num} is not open)' if num else ''}."}
    o = k["orders"][num]
    if mid and not any(it["menu"] == mid for it in o["items"]):
        return {"ok": False, "order": o["num"],
                "say": f"Order {num} has no {BY_ID[mid]['name'].lower()}. It has "
                       + ", ".join(BY_ID[it["menu"]]["name"].lower() for it in o["items"]) + "."}
    label = BY_ID[mid]["name"].lower() if mid else None

    if intent == "start":
        started, skipped = start_timers(k, o, mid, minutes)
        if not started:
            if mid:
                return {"ok": False, "order": o["num"], "say": f"{BY_ID[mid]['name']} doesn't need a timer. Say a time, like \"5 minutes\", to set one."}
            return {"ok": False, "order": o["num"], "say": f"Nothing on order {num} needs a timer right now."}
        parts = [f"{item_name(it).lower()}, {fmt_left(it['timer']['mins'] * 60)}" for it in started]
        log(k, f"▷ #{num} timer: " + "; ".join(parts))
        return {"ok": True, "order": o["num"], "say": f"Order {num}, {o['guest'].split()[0]}: " + "; ".join(parts) + "."}
    if intent == "stop":
        stopped = stop_timers(o, mid)
        log(k, f"■ #{num} timer stopped" + (f" ({label})" if label else ""))
        return {"ok": bool(stopped), "order": o["num"],
                "say": f"Stopped the {label or 'timers'} on order {num}." if stopped else f"No timer running on order {num}."}
    if intent == "add":
        changed = add_time(k, o, mid, minutes)
        if not changed:
            return {"ok": False, "order": o["num"], "say": f"No timer running on order {num} to add time to."}
        log(k, f"+ #{num} +{minutes:g} min")
        return {"ok": True, "order": o["num"], "say": f"Added {fmt_left(minutes * 60)} to order {num}."}
    if intent == "query":
        bits = []
        for it in o["items"]:
            left = remaining_s(it)
            if left is not None and not it["done"]:
                bits.append(f"{BY_ID[it['menu']]['name'].lower()} has {fmt_left(left)} left")
            elif it["done"] and it["timer"]:
                bits.append(f"{BY_ID[it['menu']]['name'].lower()} is up")
        status = STATUS_LABEL[o["status"]].lower()
        if o["check"] and not o["check"]["ok"]:
            status += ". Last check: " + o["check"]["reason"].lower()
        return {"ok": True, "order": o["num"], "say": f"Order {num} for {o['guest'].split()[0]} is {status}"
                + (". " + "; ".join(bits) if bits else "") + "."}
    if intent == "ready":
        if mid:
            mark_items_done(o, mid)
            log(k, f"✓ #{num} {label} up")
            return {"ok": True, "order": o["num"], "say": f"{BY_ID[mid]['name']} for order {num} is up."}
        set_status(o, "ready")
        log(k, f"✓ #{num} marked ready")
        return {"ok": True, "order": o["num"], "say": f"Order {num} is ready. Put it under the camera to check it."}
    if intent == "picked_up":
        set_status(o, "picked_up")
        log(k, f"→ #{num} picked up")
        return {"ok": True, "order": o["num"], "say": f"Order {num} for {o['guest'].split()[0]} is out the door."}
    if intent == "cancel":
        stop_timers(o)
        o["status"] = "cancelled"
        log(k, f"⨯ #{num} cancelled")
        return {"ok": True, "order": o["num"], "say": f"Order {num} for {o['guest'].split()[0]} is cancelled."}
    if intent == "reopen":
        stop_timers(o)
        o["status"], o["check"] = "new", None
        for it in o["items"]:
            it["done"] = False
        log(k, f"↺ #{num} reopened")
        return {"ok": True, "order": o["num"], "say": f"Order {num} is back to new."}
    if intent == "show":
        return {"ok": True, "order": o["num"], "say": f"Order {num}, {o['guest']}."}
    return {"ok": False, "say": "I didn't catch that."}
