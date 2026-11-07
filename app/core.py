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


PACK_MIN = 2  # bagging and labelling once the last dish is up


def new_order(num, guest, source, items, courier=None, placed=None, pickup_at=None):
    o = {
        "num": num, "guest": guest, "source": source, "courier": courier,
        "placed": placed or now_ms(), "status": "new", "check": None,
        "items": [{"uid": f"{num}-{i}", "menu": m, "qty": q, "mod": mod, "minutes": cook_minutes(m, mod),
                   "timer": None, "done": False} for i, (m, q, mod) in enumerate(items)],
    }
    set_pickup(o, pickup_at or o["placed"] + (prep_minutes(o) + 8) * 60_000)
    return o


def add_order(k, guest, source, lines, courier=None, pickup_in=None):
    """Put a new ticket on the rail, from the new-order form or from voice. Returns the order."""
    num = max([int(n) for n in k["orders"]] + [40]) + 1
    o = new_order(num, guest, source, lines, courier)
    if pickup_in:
        set_pickup(o, now_ms() + max(1, min(pickup_in, 240)) * 60_000)
    k["orders"][str(num)] = o
    log(k, f"+ #{num} {guest} ({source})")
    return o


def prep_minutes(o) -> int:
    """Dishes cook side by side, so an order takes as long as its slowest dish, plus packing."""
    return max((it["minutes"] or 0) for it in o["items"]) + PACK_MIN


def set_pickup(o, at_ms):
    """When the Dasher (or guest) arrives, and so when the kitchen has to start the order to hit it."""
    o["pickup_at"] = int(at_ms)
    o["start_at"] = int(at_ms - prep_minutes(o) * 60_000)
    o["nudged"] = o["start_at"] <= now_ms()  # already due: the ticket shows it, no call-out needed


def demo_orders():
    """The 25 sample tickets, staggered so one comes due to start about every two minutes."""
    t, out = now_ms(), {}
    for n, g, s, ago, c, items in DEMO_ORDERS:
        o = new_order(n, g, s, items, c, t - ago * 60_000)
        set_pickup(o, t + ((n - 41) * 2 - 4 + prep_minutes(o)) * 60_000)
        out[str(n)] = o
    return out


def upgrade(k):
    """Tickets saved before pickup times existed get a sensible one."""
    for o in k["orders"].values():
        if "pickup_at" not in o:
            set_pickup(o, o["placed"] + (prep_minutes(o) + 8) * 60_000)


def due_to_start(k, t=None):
    """New orders whose start time just arrived. Each is returned once."""
    t = t or now_ms()
    due = [o for o in k["orders"].values() if o["status"] == "new" and not o.get("nudged") and o.get("start_at", t + 1) <= t]
    for o in due:
        o["nudged"] = True
    return sorted(due, key=lambda o: o["start_at"])


def mins_until(ms, t=None) -> int:
    return round((ms - (t or now_ms())) / 60_000)


def courier_word(o) -> str:
    return ("Dasher" if o["source"] == "DoorDash" else "Courier") if o.get("courier") else "Pickup"


def plural(n, word="minute") -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def pickup_say(o) -> str:
    p, who = mins_until(o["pickup_at"]), courier_word(o)
    return f"{who} in {plural(p)}." if p > 0 else f"{who} is due now." if p == 0 else f"{who} was due {plural(-p)} ago."


def start_say(o) -> str:
    s = mins_until(o["start_at"])
    return f"Start it in {plural(s)}." if s > 0 else "Start it now." if s == 0 else f"Start it now, it's {plural(-s)} late."


def timing_say(o) -> str:
    """'Dasher in 12 minutes. Start it in 3 minutes.' for whatever applies to this order right now."""
    if not o.get("pickup_at"):
        return ""
    return pickup_say(o) + (" " + start_say(o) if o["status"] == "new" else "")


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


def _picked(it, menu_id=None, mod=None) -> bool:
    """Is this the item a command means? "the medium rare steak" narrows by doneness as well as dish."""
    return (not menu_id or it["menu"] == menu_id) and (not mod or (it["mod"] or "").lower() == mod)


def start_timers(k, o, menu_id=None, minutes=None, mod=None):
    """Start (or restart) timers on an order. Returns the items started and the ones that need no timer."""
    started, skipped = [], []
    for it in o["items"]:
        if not _picked(it, menu_id, mod):
            continue
        mins = minutes if minutes is not None else it["minutes"]
        if not mins:
            skipped.append(it)
            continue
        if not menu_id and not mod and minutes is None and it["timer"] and not it["done"]:
            continue  # "fire order 45" leaves running timers alone
        it["timer"] = {"start": now_ms(), "secs": mins * 60 / max(k.get("speed", 1), 1), "mins": mins}
        it["done"] = False
        started.append(it)
    _refresh_status(o)
    return started, skipped


def stop_timers(o, menu_id=None, mod=None):
    stopped = []
    for it in o["items"]:
        if _picked(it, menu_id, mod) and it["timer"]:
            it["timer"], it["done"] = None, False
            stopped.append(it)
    if o["status"] == "cooking" and not any(it["timer"] for it in o["items"]):
        o["status"] = "new"
    return stopped


def add_time(k, o, menu_id, minutes, mod=None):
    changed = []
    for it in o["items"]:
        if _picked(it, menu_id, mod) and it["timer"]:
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
    ("eta", r"\b(arriv\w*|minutes? (away|out)|push(ed|ing)?|back|delay(ed)?|running late|late|later|sooner|earlier|"
            r"move\w*|up by|pick ?up (time )?(is )?(in|at))\b"),  # only counts with a number of minutes
    ("picked_up", r"\b(picked up|pick(ed)? ?up|handed (it )?off|hand(ed)? off|handoff|out the door|delivered|collected)\b"),
    ("add", r"\b(add|plus|extend|another|more time)\b"),
    ("cancel", r"\b(cancel|cancelled|canceled|void|scratch)\b"),
    ("stop", r"\b(stop|pause|kill|reset|clear)\b"),
    ("query", r"\b(how long|time left|how much (time|longer)|status|where (are|is)|what'?s left|check on|eta)\b"),
    ("next", r"\b(what'?s next|next (order|ticket|up)|oldest|start next|(what|which)( order)? (should|do) (i|we) (start|fire|cook)|"
             r"(what|which)\b[\w' ]{0,30}\b(have|need|needs|got|should|supposed|ought|do|can|gotta)( (i|we|you))? (to )?(be )?"
             r"(start|fire|cook|drop|make)\w*|anything to (start|fire)|"
             r"(what|which)\b[\w' ]{0,30}\bis (due|up next))\b"),
    ("reopen", r"\b(reopen|re open|undo|back to new)\b"),
    ("start", r"\b(start|fire|begin|cook|drop|timer|put on|throw on|go on)\b"),
    ("show", r"\b(show|open|pull up|find|highlight)\b"),
    ("ready", r"\b(ready|done|finished|complete|bump|plated|up)\b"),
]
_RANK = {name: i for i, (name, _) in enumerate(_INTENTS)}
_LATER = r"\b(push(ed|ing)?|back|delay(ed)?|late|later)\b"
_SOONER = r"\b(sooner|earlier|up by|move\w* (\w+ ){0,3}up)\b"
# "show me the orders that are cooking" / "which tickets have burgers" change what the rail shows, not the orders
_VIEW_VERB = (r"\b(show|only|filter|display|list|see|view|pull up|find|which|what (orders|tickets)|how many|anything|"
              r"any (orders?|tickets?)|are there|is there|do we have|what'?s|what (is|are)|are we|who)\b")
_ACTING = r"\b(fire|timers?|stop|add|push|delay|move|mark|reopen)\b"
_VIEW_STATUS = [("cooking", r"cooking|on the (fire|grill)|in progress|firing"), ("ready", r"ready|done|finished|plated"),
                ("issue", r"issues?|problems?|wrong|missing|flagged"), ("verified", r"checked|verified|good to go|passed"),
                ("out", r"picked up|handed off|out the door|gone|delivered|cancell?ed|closed"),
                ("new", r"new|not started|waiting|to start|needs? to (start|be started)|haven'?t started")]
_VIEW_SOURCES = {"DoorDash": r"door ?dash", "Uber Eats": r"uber( eats)?", "Grubhub": r"grub ?hub", "Phone": r"phone",
                 "Website": r"website|online", "Walk-in": r"walk ?in|counter"}
_VIEW_LATE = r"\b(late|behind|overdue)\b"
_ALL = r"\b(all|every|each|any|everything)\b"
_BULK = ("start", "stop", "add", "ready", "query")  # safe to run across the rail; cancel and hand-off need a number
_WAITING = ("new", "cooking", "ready", "issue")
_DONENESS = r"\b(medium rare|medium well|well done|rare|medium)\b"  # longest first: "medium rare" is not "medium"
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


# "We just got an order for Ben Frank: two fried shrimp, a side of fries and a smashburger" rings in a ticket
_NEW_ORDER = (r"\b(new order|(got|get|getting|taking|take|ring|ringing|put|putting|enter|place|placing|incoming|add|adding)"
              r"( \w+){0,3} orders?|orders? (just )?(came|comes|coming) in)\b")
_NAME_STOP = {"it", "its", "it's", "with", "and", "who", "that", "they", "he", "she", "please", "for", "the", "a", "an",
              "of", "is", "wants", "want", "ordered", "ordering", "on", "from", "via", "through", "pickup", "pick", "in"}
_COUNT = {"a": 1, "an": 1, "one": 1, "single": 1, "double": 2, "couple": 2, "pair": 2}


def _parse_new_order(text: str, t: str):
    """The guest, app and dishes in "new order for <name> ..."; None unless it names a guest and a dish."""
    if not re.search(_NEW_ORDER, t) or re.search(r"\b(order|ticket) (number )?\d", t):
        return None
    lines, taken = [], []
    for alias, mid in _ALIASES:
        for m in re.finditer(rf"\b{re.escape(alias)}(?:e?s)?\b", t):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            before = t[:m.start()].split()[-4:]
            qty = next((int(w) if w.isdigit() else _COUNT[w] for w in reversed(before)
                        if (w.isdigit() and 0 < int(w) <= 20) or w in _COUNT), 1)
            near = " ".join(before) + " " + " ".join(t[m.end():].split()[:2])
            mod = (re.search(_DONENESS, near) or [None])[0] if isinstance(BY_ID[mid]["cook"], dict) else None
            lines.append((m.start(), mid, qty, mod))
    words = re.sub(r"[^\w\s']", " ", text).split()
    menu_words = {w for a, _ in _ALIASES for w in a.split()}
    name = []
    for i, w in enumerate(words):
        if w.lower() in ("for", "under", "named") and not name:
            for w2 in words[i + 1:i + 4]:
                lw = w2.lower()
                if lw in _NAME_STOP or lw in menu_words or lw.rstrip("s") in menu_words or lw in _UNITS or w2.isdigit():
                    break
                name.append(w2[:1].upper() + w2[1:])
            if name:
                break
    if not lines or not name:
        return None
    source = next((s for s, rx in _VIEW_SOURCES.items() if re.search(rf"\b({rx})\b", t)), "Walk-in")
    mins = re.search(r"\b(pick ?up|ready|here|arriv\w*)\b.{0,20}?\b(\d+) ?(minutes?|mins?)\b", t)
    return {"guest": " ".join(name)[:40], "source": source, "pickup_in": int(mins.group(2)) if mins else None,
            "items": [(mid, q, mod) for _, mid, q, mod in sorted(lines)]}


def parse_commands(text: str, k) -> list[dict]:
    """Turn what someone said at the pass into one command per order mentioned.

    "Start the timer for order 41 smashburger, order 43 margherita pizza, and order 46 we can cancel that" gives
    three commands. Each dish, verb and time goes to the order named closest to it; an order with no verb of its
    own takes the one before it.
    """
    t = _normalize(text)
    new = _parse_new_order(text, t)
    if new:
        return [{"intent": "new_order", "order": None, "item": None, "items": [], "minutes": None, "text": text, "new": new}]
    refs = _order_refs(t, k)
    slots = [{"intents": set(), "items": [], "minutes": None, "shift": None} for _ in refs or [None]]
    pick = (lambda a, b, prefer=None: _nearest(refs, a, b, prefer) or 0) if refs else (lambda a, b, prefer=None: 0)

    for m in re.finditer(_MINUTES, t):
        mins = int(m.group(1)) + (0.5 if "half" in m.group(0) else 0) if m.group(1) else 0.5
        slots[pick(m.start(), m.end())]["minutes"] = mins
    taken = []
    for alias, mid in _ALIASES:
        for m in re.finditer(rf"\b{re.escape(alias)}(?:e?s)?\b", t):  # "cheeseburgers", "garlic breads"
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            has = lambda num, mid=mid: num in k["orders"] and any(it["menu"] == mid for it in k["orders"][num]["items"])
            i = (_nearest(refs, m.start(), m.end(), has) if refs else None)
            i = pick(m.start(), m.end()) if i is None else i
            slots[i]["items"].append((m.start(), mid))
    mod = (re.search(_DONENESS, t) or [None])[0]
    said = re.sub(_DONENESS, lambda m: " " * len(m.group(0)), t)  # "well done" is a doneness, not "done"
    for name, rx in _INTENTS:
        for m in re.finditer(rx, said):
            slots[pick(m.start(), m.end())]["intents"].add(name)
    for m in re.finditer(_LATER + "|" + _SOONER, t):  # "push 45 back 10 minutes" moves the pickup; "5 minutes away" sets it
        slots[pick(m.start(), m.end())]["shift"] = -1 if re.fullmatch(_SOONER, m.group(0)) else 1

    cmds, prev = [], None
    for i, slot in enumerate(slots):
        intents = slot["intents"]
        items = [mid for _, mid in sorted(slot["items"])]
        if "cancel" in intents:  # "cancel the timer" / "cancel the steak" stop timers; "cancel order 46" voids it
            intents = (intents - {"cancel", "start"}) | ({"stop"} if items or "start" in intents else {"cancel"})
        if slot["minutes"] is None:
            intents -= {"add", "eta"}
        intent = min(intents, key=_RANK.get) if intents else prev
        if intent == "next" and refs:
            intent = prev
        prev = intent or prev
        cmds.append({"intent": intent, "order": refs[i][2] if refs else None, "item": items[0] if items else None,
                     "items": items, "minutes": slot["minutes"], "shift": slot["shift"],
                     "mod": mod if items else None, "text": text})
    if not refs and re.search(_VIEW_VERB, t) and not re.search(_ACTING, t) and not slots[0]["intents"] & {"next", "eta"}:
        items = list(dict.fromkeys(mid for _, mid in sorted(slots[0]["items"])))
        return [{"intent": "view", "order": None, "item": None, "items": [], "minutes": None, "text": text,
                 "view": _parse_view(t, items)}]
    if not refs and re.search(_ALL, t):
        return _for_every_order(cmds[0], k)
    return cmds


def _parse_view(t, items) -> dict:
    status = next((s for s, rx in _VIEW_STATUS if re.search(rf"\b({rx})\b", t)), None)
    source = next((s for s, rx in _VIEW_SOURCES.items() if re.search(rf"\b({rx})\b", t)), None)
    return {"status": status, "items": items, "source": source, "late": bool(re.search(_VIEW_LATE, t))}


def is_late(o, t=None) -> bool:
    """Should have been started a minute ago, or its pickup time has passed."""
    t = t or now_ms()
    if o["status"] in ("picked_up", "cancelled"):
        return False
    return (o["status"] == "new" and o.get("start_at", t) < t - 60_000) or o.get("pickup_at", t) < t


def in_view(o, v, t=None) -> bool:
    """The dashboard applies the same rules (inView in dashboard.js) so the rail stays filtered as tickets change."""
    st, closed = v.get("status") or "open", o["status"] in ("picked_up", "cancelled")
    if (st == "open" and closed) or (st == "out" and not closed) or (st not in ("open", "out") and o["status"] != st):
        return False
    if v.get("items") and not any(it["menu"] in v["items"] for it in o["items"]):
        return False
    if v.get("source") and o["source"] != v["source"]:
        return False
    return not v.get("late") or is_late(o, t)


_VIEW_WORD = {"cooking": "cooking", "new": "new", "ready": "ready", "issue": "flagged", "verified": "checked", "out": "closed"}


def say_orders(orders) -> str:
    """'order 41 for Maya and order 45 for John', or just the numbers once there are more than three."""
    names = [f"order {o['num']} for {o['guest'].split()[0]}" for o in orders] if len(orders) <= 3 \
        else [str(o["num"]) for o in orders[:8]] + ([f"{len(orders) - 8} more"] if len(orders) > 8 else [])
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def view_say(v, matches) -> str:
    n = len(matches)
    if not (v.get("status") or v.get("items") or v.get("source") or v.get("late")):
        return f"Showing all {plural(n, 'open order')}."
    word = _VIEW_WORD.get(v.get("status"), "open" if not (v.get("late") or v.get("items") or v.get("source")) else "")
    desc = " ".join(w for w in ("late" if v.get("late") else "", word, "order") if w)
    tail = (" with " + " or ".join(BY_ID[m]["name"].lower() for m in v["items"]) if v.get("items") else "") \
        + (f" from {v['source']}" if v.get("source") else "")
    if not n:
        return f"No {desc}s{tail} right now. Say \"show everything\" to see the whole rail."
    return f"Showing {n} {desc}{'s' * (n != 1)}{tail}: {say_orders(matches)}."


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
        has = [mid for mid in wanted if any(_picked(it, mid, cmd.get("mod")) for it in o["items"])]
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
    return {"ok": bool(ok), "say": " ".join(r["say"] for r in results), "order": (ok or results)[-1].get("order"),
            "view": next((r["view"] for r in results if "view" in r), None), "results": results}


def run_command(k, cmd) -> dict:
    """Apply a parsed command. Returns {ok, say, order} where say is read aloud at the pass."""
    intent, num, mid, minutes = cmd["intent"], cmd["order"], cmd["item"], cmd["minutes"]
    if intent == "new_order":
        n = cmd["new"]
        o = add_order(k, n["guest"], n["source"], n["items"], pickup_in=n["pickup_in"])
        dishes = ", ".join(f"{it['qty']} " + re.sub(r"(?<![sp])( \(|$)", r"s\1", item_name(it).lower(), count=1)
                           if it["qty"] > 1 else item_name(it).lower() for it in o["items"])  # "2 carnitas tacos", "2 fried shrimp"
        return {"ok": True, "order": o["num"], "new_order": o["num"],
                "say": f"New order {o['num']} for {o['guest'].split()[0]}: {dishes}. {timing_say(o)}"}
    if intent == "view":
        matches = sorted((o for o in k["orders"].values() if in_view(o, cmd["view"])), key=lambda o: o["num"])
        return {"ok": True, "view": cmd["view"], "count": len(matches), "say": view_say(cmd["view"], matches)}
    if intent == "next":
        to_start = sorted((o for o in k["orders"].values() if o["status"] == "new" and o.get("start_at")),
                          key=lambda o: o["start_at"])
        due = [o for o in to_start if o["start_at"] <= now_ms() + 30_000]
        many = re.search(r"\b(orders|tickets|ones|anything|everything|now)\b", _normalize(cmd["text"]))
        if len(due) > 1 and many:  # "what orders should I fire up now": every ticket whose start time has come
            later = to_start[len(due)] if len(to_start) > len(due) else None
            return {"ok": True, "order": due[0]["num"],
                    "say": f"Start {len(due)} orders now: {say_orders(due)}."
                           + (f" Then order {later['num']} in {plural(mins_until(later['start_at']))}." if later else "")}
        if to_start:  # the order whose pickup time leaves the least slack
            o = to_start[0]
            return {"ok": True, "order": o["num"],
                    "say": f"Order {o['num']} for {o['guest'].split()[0]} is next. {start_say(o)} {pickup_say(o)}"}
        waiting = sorted((o for o in k["orders"].values() if o["status"] in _WAITING), key=lambda o: o["placed"])
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
        what = (f"{cmd['mod']} " if cmd.get("mod") else "") \
            + (" or ".join(BY_ID[m]["name"].lower() for m in cmd.get("items") or []) or "anything")
        return {"ok": False, "say": f"No open order has {what}."}
    if num is None or num not in k["orders"]:
        return {"ok": False, "say": f"Which order? I didn't hear an order number{f' ({num} is not open)' if num else ''}."}
    o = k["orders"][num]
    if mid and not any(it["menu"] == mid for it in o["items"]):
        return {"ok": False, "order": o["num"],
                "say": f"Order {num} has no {BY_ID[mid]['name'].lower()}. It has "
                       + ", ".join(BY_ID[it["menu"]]["name"].lower() for it in o["items"]) + "."}
    mod = cmd.get("mod")
    if mid and mod and not any(_picked(it, mid, mod) for it in o["items"]):
        return {"ok": False, "order": o["num"], "say": f"Order {num} has no {mod} {BY_ID[mid]['name'].lower()}. It's "
                + ", ".join(it["mod"] or "no doneness" for it in o["items"] if it["menu"] == mid) + "."}
    label = (f"{mod} " if mod else "") + BY_ID[mid]["name"].lower() if mid else None

    if intent == "eta":
        base = o.get("pickup_at") or now_ms()
        at = base + cmd.get("shift") * minutes * 60_000 if cmd.get("shift") else now_ms() + minutes * 60_000
        set_pickup(o, max(at, now_ms()))
        log(k, f"⇢ #{num} {courier_word(o).lower()} in {plural(mins_until(o['pickup_at']), 'min')}")
        return {"ok": True, "order": o["num"], "say": f"Order {num}: " + timing_say(o)}
    if intent == "start":
        started, skipped = start_timers(k, o, mid, minutes, mod)
        if not started:
            if mid:
                return {"ok": False, "order": o["num"], "say": f"{BY_ID[mid]['name']} doesn't need a timer. Say a time, like \"5 minutes\", to set one."}
            return {"ok": False, "order": o["num"], "say": f"Nothing on order {num} needs a timer right now."}
        parts = [f"{item_name(it).lower()}, {fmt_left(it['timer']['mins'] * 60)}" for it in started]
        log(k, f"▷ #{num} timer: " + "; ".join(parts))
        return {"ok": True, "order": o["num"], "say": f"Order {num}, {o['guest'].split()[0]}: " + "; ".join(parts) + "."}
    if intent == "stop":
        stopped = stop_timers(o, mid, mod)
        log(k, f"■ #{num} timer stopped" + (f" ({label})" if label else ""))
        return {"ok": bool(stopped), "order": o["num"],
                "say": f"Stopped the {label or 'timers'} on order {num}." if stopped else f"No timer running on order {num}."}
    if intent == "add":
        changed = add_time(k, o, mid, minutes, mod)
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
                + (". " + "; ".join(bits) if bits else "") + ". " + timing_say(o)}
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
