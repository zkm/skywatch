from app.aircraft import FORGET_AFTER_S, AircraftWatch, pick

FEED = {"ac": [
    {"hex": "ae0001", "t": "E6", "r": "164386", "flight": "GOTO123 ", "lat": 41.1, "lon": -95.9,
     "alt_baro": 25000, "gs": 410, "track": 90, "seen_pos": 2},
    {"hex": "adfdf9", "t": "B744", "r": "73-1677", "lat": 39.0, "lon": -104.7, "alt_baro": "ground",
     "seen": 5},
    {"hex": "ae0002", "t": "K35R", "r": "57-1473", "lat": 32.9, "lon": -86.4, "alt_baro": 22975},
    {"hex": "ae0003", "t": "B52", "r": "60-0001", "lat": None, "lon": None, "seen": 1},
]}


def test_pick_keeps_watched_types_and_e4b_tails():
    got = {a["hex"]: a for a in pick(FEED, now=1000)}
    assert set(got) == {"ae0001", "adfdf9", "ae0003"}           # tanker dropped
    assert got["ae0001"]["label"] == "E-6B Mercury"
    assert got["ae0001"]["callsign"] == "GOTO123"
    assert got["adfdf9"]["label"] == "E-4B Nightwatch"          # matched by registration
    assert got["adfdf9"]["alt"] == "ground"
    assert got["ae0001"]["seen_at"] == 998
    assert got["ae0003"]["seen_at"] == 999                       # no position: last message


def test_trails_grow_and_stale_aircraft_expire():
    w = AircraftWatch("http://example.invalid", 30, lambda s: None)
    w.update(FEED, now=1000)
    moved = {"ac": [{**FEED["ac"][0], "lat": 41.2}]}
    snap = w.update(moved, now=1030)
    e6 = next(a for a in snap["aircraft"] if a["hex"] == "ae0001")
    assert e6["trail"] == [[41.1, -95.9], [41.2, -95.9]]
    assert any(a["hex"] == "ae0003" for a in snap["aircraft"])  # still within the window
    snap = w.update(moved, now=1030 + FORGET_AFTER_S + 10)
    assert [a["hex"] for a in snap["aircraft"]] == ["ae0001"]
