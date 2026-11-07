from app import core, vision
from tests.make_photo import photo


def test_cards_in_a_photo_match_the_ticket(tmp_path):
    out = tmp_path / "plate.jpg"
    photo(["ribeye", "mash"], out)
    found, tag = vision.read_markers(vision.prepare(out.read_bytes(), 1600))
    assert found == {"ribeye": 1, "mash": 1} and tag is None
    r = core.match_plate(core.new_kitchen("t"), found, tag)
    assert r["order"] == 45 and [x["id"] for x in r["missing"]] == ["lobster"]


def test_duplicate_cards_count_twice(tmp_path):
    out = tmp_path / "plate.jpg"
    photo(["taco", "taco", "taco", "rice"], out)
    found, _ = vision.read_markers(vision.prepare(out.read_bytes(), 1600))
    assert found == {"taco": 3, "rice": 1}
