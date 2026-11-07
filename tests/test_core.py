from app import core


def kitchen():
    return core.new_kitchen("Test", demo=True)


def run(k, text):
    return core.run_command(k, core.parse_command(text, k))


def test_parse_variants():
    k = kitchen()
    cases = {
        "start the timer for order 45 steak": ("start", "45", "ribeye", None),
        "Start the timer for order forty-five steak": ("start", "45", "ribeye", None),
        "fire order 45": ("start", "45", None, None),
        "start a 15 minute timer for order 45 lobster": ("start", "45", "lobster", 15),
        "stop the timer on 45": ("stop", "45", None, None),
        "add 2 minutes to order 45 steak": ("add", "45", "ribeye", 2),
        "how long on order 45": ("query", "45", None, None),
        "how much time left on 45": ("query", "45", None, None),
        "order 45 is ready": ("ready", "45", None, None),
        "the steak for 45 is done": ("ready", "45", "ribeye", None),
        "order 45 picked up": ("picked_up", "45", None, None),
        "pull up order 52": ("show", "52", None, None),
        "start John's steak": ("start", "45", "ribeye", None),
        "what's next": ("next", None, None, None),
    }
    for text, want in cases.items():
        c = core.parse_command(text, k)
        assert (c["intent"], c["order"], c["item"], c["minutes"]) == want, (text, c)


def test_steak_timer_uses_doneness():
    k = kitchen()
    r = run(k, "start the timer for order 45 steak")
    assert r["ok"] and "15 minutes" in r["say"]
    o = k["orders"]["45"]
    assert o["status"] == "cooking"
    steak = o["items"][0]
    assert steak["timer"]["mins"] == 15 and abs(core.remaining_s(steak) - 900) < 2


def test_fire_skips_untimed_items_and_ticks_to_ready():
    k = kitchen()
    run(k, "fire order 45")
    o = k["orders"]["45"]
    assert [bool(it["timer"]) for it in o["items"]] == [True, True, False]  # mash needs no timer
    later = core.now_ms() + 16 * 60_000
    done = core.tick(k, later)
    assert {it["menu"] for _, it in done} == {"ribeye", "lobster"}
    assert o["status"] == "ready"


def test_untimed_item_and_wrong_item():
    k = kitchen()
    assert not run(k, "start order 45 mashed potatoes")["ok"]
    r = run(k, "start order 45 pizza")
    assert not r["ok"] and "no margherita pizza" in r["say"].lower()


def test_match_missing_lobster():
    k = kitchen()
    r = core.match_plate(k, {"ribeye": 1, "mash": 1})
    assert r["order"] == 45 and not r["ok"]
    assert [x["id"] for x in r["missing"]] == ["lobster"] and not r["extra"]


def test_match_complete_and_extra():
    k = kitchen()
    r = core.match_plate(k, {"ribeye": 1, "lobster": 1, "mash": 1})
    assert r["order"] == 45 and r["ok"]
    r = core.match_plate(k, {"pizza": 1, "caesar": 1, "soda": 1})
    assert r["order"] == 43 and [x["id"] for x in r["extra"]] == ["soda"]


def test_quantities_and_tag():
    k = kitchen()
    r = core.match_plate(k, {"taco": 2, "rice": 1})
    assert r["order"] == 47 and r["missing"][0]["qty"] == 1
    r = core.match_plate(k, {"ribeye": 1}, order_tag="65")
    assert r["order"] == 65 and r["by_tag"]


def test_record_check_sets_status():
    k = kitchen()
    core.record_check(k, core.match_plate(k, {"ribeye": 1, "mash": 1}))
    assert k["orders"]["45"]["status"] == "issue"
    core.record_check(k, core.match_plate(k, {"ribeye": 1, "lobster": 1, "mash": 1}))
    assert k["orders"]["45"]["status"] == "verified"
    run(k, "order 45 picked up")
    assert k["orders"]["45"]["status"] == "picked_up"
    # picked-up orders are no longer candidates
    assert core.match_plate(k, {"ribeye": 1, "lobster": 1, "mash": 1})["order"] != 45


def test_empty_and_unmatched():
    k = kitchen()
    assert core.match_plate(k, {})["order"] is None
    k["orders"] = {}
    assert core.match_plate(k, {"pizza": 1})["order"] is None


def test_several_orders_in_one_breath():
    k = kitchen()
    text = "Start the timer for order 41 Smashburger order 43 margarita pizza and then order 46 we can cancel that"
    cmds = core.parse_commands(text, k)
    assert [(c["intent"], c["order"], c["item"]) for c in cmds] == [
        ("start", "41", "burger"), ("start", "43", "pizza"), ("cancel", "46", None)]
    r = core.run_commands(k, cmds)
    assert r["ok"] and "41" in r["say"] and "43" in r["say"] and "cancelled" in r["say"]
    assert k["orders"]["41"]["items"][0]["timer"] and k["orders"]["43"]["items"][0]["timer"]
    assert k["orders"]["46"]["status"] == "cancelled"
    assert core.match_plate(k, {"pasta": 1, "bread": 1})["order"] != 46  # cancelled orders aren't matched
    run(k, "reopen order 46")
    assert k["orders"]["46"]["status"] == "new"


def test_dish_goes_to_nearest_order():
    k = kitchen()
    cmds = core.parse_commands("start the steak for order 45 and the lobster for order 65", k)
    assert [(c["order"], c["items"]) for c in cmds] == [("45", ["ribeye"]), ("65", ["lobster"])]
    cmds = core.parse_commands("fire order 45 steak and lobster", k)
    assert cmds[0]["items"] == ["ribeye", "lobster"]
    r = core.run_commands(k, cmds)
    assert [bool(it["timer"]) for it in k["orders"]["45"]["items"]] == [True, True, False]
    cmds = core.parse_commands("start order 41 and stop order 53", k)
    assert [(c["intent"], c["order"]) for c in cmds] == [("start", "41"), ("stop", "53")]
    assert core.parse_command("cancel the timer on 45", k)["intent"] == "stop"
    assert core.parse_command("start a 15 minute timer for order 45 lobster", k)["minutes"] == 15


def test_every_order_with_a_dish():
    k = kitchen()
    cmds = core.parse_commands("For all orders with garlic bread start the garlic bread timer", k)
    assert [(c["intent"], c["order"], c["items"]) for c in cmds] == [
        ("start", "46", ["bread"]), ("start", "54", ["bread"]), ("start", "56", ["bread"])]
    r = core.run_commands(k, cmds)
    assert r["ok"] and all(n in r["say"] for n in ("46", "54", "56"))
    for n in ("46", "54", "56"):
        assert [it["menu"] for it in k["orders"][n]["items"] if it["timer"]] == ["bread"]
    assert core.parse_commands("fire the fries on every ticket", k)[0]["order"] == "41"
    assert len(core.parse_commands("stop all the garlic bread timers", k)) == 3
    assert core.parse_commands("start the timer on any order with salmon nigiri", k)[0]["order"] == "48"
    empty = core.new_kitchen("Empty", demo=False)
    assert "No open order has garlic bread" in core.run_commands(empty, core.parse_commands("start all the garlic bread", empty))["say"]
    r = core.run_commands(k, core.parse_commands("cancel all orders", k))
    assert not r["ok"] and all(o["status"] != "cancelled" for o in k["orders"].values())
