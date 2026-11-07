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


def test_start_time_comes_from_pickup_and_slowest_dish():
    k = kitchen()
    o = k["orders"]["45"]  # ribeye medium rare 15 min is the slowest, plus 2 to pack
    assert core.prep_minutes(o) == 17
    assert o["pickup_at"] - o["start_at"] == 17 * 60_000
    starts = [k["orders"][str(n)]["start_at"] for n in range(41, 66)]
    assert starts == sorted(starts)  # demo comes due one after another
    assert core.mins_until(k["orders"]["43"]["start_at"]) == 0


def test_due_to_start_calls_each_order_once():
    k = kitchen()
    later = core.now_ms() + 3 * 60_000
    due = [o["num"] for o in core.due_to_start(k, later)]
    assert due == [44]  # 41-43 were already due when the demo loaded
    assert core.due_to_start(k, later) == []
    run(k, "fire order 45")
    assert 45 not in [o["num"] for o in core.due_to_start(k, core.now_ms() + 60 * 60_000)]


def test_dasher_eta_by_voice():
    k = kitchen()
    r = run(k, "Dasher for order 45 is 5 minutes away")
    assert r["ok"] and core.mins_until(k["orders"]["45"]["pickup_at"]) == 5
    assert "Start it now, it's 12 minutes late" in r["say"]
    before = k["orders"]["46"]["pickup_at"]
    run(k, "push order 46 back 10 minutes")
    assert k["orders"]["46"]["pickup_at"] - before == 10 * 60_000
    run(k, "move order 46 up 5 minutes")
    assert k["orders"]["46"]["pickup_at"] - before == 5 * 60_000
    assert core.parse_command("order 45 picked up", k)["intent"] == "picked_up"
    assert core.parse_command("order 45 pickup in 20 minutes", k)["intent"] == "eta"
    assert core.parse_command("order 45 is running late", k)["intent"] != "eta"  # no minutes, nothing to move


def test_what_to_start_next():
    k = kitchen()
    for text in ("What should I start next?", "what's next"):
        r = run(k, text)
        assert r["order"] == 41 and "is next" in r["say"] and "late" in r["say"]
    run(k, "fire order 41")
    assert run(k, "what should we cook next")["order"] == 42


def test_show_changes_the_view_not_the_orders():
    k = kitchen()
    run(k, "fire order 41")
    run(k, "fire order 45")
    view = lambda text: core.run_commands(k, core.parse_commands(text, k))
    r = view("Can you show me just all of the tickets that are cooking right now")
    assert r["view"]["status"] == "cooking" and "Showing 2 cooking orders" in r["say"]
    assert [o["status"] for o in k["orders"].values()].count("cooking") == 2  # nothing changed
    r = view("show me all the orders with cheeseburgers")
    assert r["view"]["items"] == ["burger"] and r["view"]["status"] is None and "Showing 3 orders with smash burger" in r["say"]
    r = view("which tickets have garlic bread")
    assert r["view"]["items"] == ["bread"]
    r = view("only show doordash orders that are new")
    assert r["view"]["source"] == "DoorDash" and r["view"]["status"] == "new"
    r = view("show me the late orders")
    assert r["view"]["late"] and r["results"][0]["count"] >= 1
    r = view("show everything")
    assert r["view"] == {"status": None, "items": [], "source": None, "late": False} and "Showing all 25 open orders" in r["say"]
    assert core.parse_command("show order 45", k)["intent"] == "show"
    assert core.parse_command("what should I start next", k)["intent"] == "next"
    assert core.parse_command("which order should we cook next", k)["intent"] == "next"


def test_bulk_start_by_doneness():
    k = core.new_kitchen("t")
    r = core.run_commands(k, core.parse_commands("Can you start the timers for all of the steaks that are set to medium rare", k))
    assert [x["order"] for x in r["results"]] == [45]
    assert k["orders"]["45"]["status"] == "cooking" and k["orders"]["53"]["status"] == "new"
    core.run_commands(k, core.parse_commands("start order 51 well done burger", k))
    assert [bool(it["timer"]) for it in k["orders"]["51"]["items"]] == [True, False, False]
    assert "no medium rare" in core.run_command(k, core.parse_command("start order 53 medium rare steak", k))["say"]


def test_new_order_by_voice():
    k = core.new_kitchen("t")
    said = ("All right we just got one order for Ben Frank it's for two fried shrimp with a side of fries "
            "a Caesar salad and the Smashburger")
    r = core.run_commands(k, core.parse_commands(said, k))
    o = k["orders"][str(r["order"])]
    assert o["guest"] == "Ben Frank" and o["source"] == "Walk-in"
    assert [(it["menu"], it["qty"]) for it in o["items"]] == [("shrimp", 2), ("fries", 1), ("caesar", 1), ("burger", 1)]
    c = core.parse_command("new order for sarah, a medium rare steak and two cokes, DoorDash, pickup in 25 minutes", k)
    assert c["new"] == {"guest": "Sarah", "source": "DoorDash", "pickup_in": 25,
                        "items": [("ribeye", 1, "medium rare"), ("soda", 2, None)]}
    assert core.parse_command("we got order 45 ready", k)["intent"] == "ready"


def test_what_to_start_phrasings():
    k = core.new_kitchen("t")
    for said in ("What's the order that I have to start right now", "which ticket needs to be started", "what do I need to fire"):
        assert core.parse_command(said, k)["intent"] == "next", said
    assert core.parse_command("which orders are late", k)["intent"] == "view"


def test_questions_about_the_rail():
    k = kitchen()
    r = run(k, "How many orders have ribeye steak")
    assert r["view"]["items"] == ["ribeye"] and "45, 53, 60 and 65" in r["say"]
    for said in ("what is late", "anything late", "are we behind on anything"):
        assert run(k, said)["view"]["late"], said
    assert run(k, "whats ready")["view"]["status"] == "ready"
    r = run(k, "what orders should i fire up now")
    assert r["say"].startswith("Start 3 orders now: order 41 for Maya")
    assert "is next" in run(k, "what's next")["say"]


def test_order_for_a_table():
    k = kitchen()
    r = run(k, "Can you make a order for table 2 with two margarita pizzas and a soda")
    o = k["orders"][str(r["order"])]
    assert o["guest"] == "Table 2" and [(it["menu"], it["qty"]) for it in o["items"]] == [("pizza", 2), ("soda", 1)]
    assert "for Table 2" in r["say"]
    assert run(k, "fire table 2")["order"] == o["num"] and o["status"] == "cooking"
