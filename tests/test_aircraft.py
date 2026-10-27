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


def test_on_appear_fires_once_per_sighting():
    seen = []
    w = AircraftWatch("http://example.invalid", 30, lambda s: None, seen.append)
    w.update(FEED, now=1000)
    assert sorted(a["hex"] for a in seen) == ["adfdf9", "ae0001", "ae0003"]
    seen.clear()
    w.update(FEED, now=1030)                                   # still up: no repeat
    assert seen == []
    w.update({"ac": []}, now=1030 + FORGET_AFTER_S + 10)      # forgotten...
    w.update({"ac": [FEED["ac"][1]]}, now=1030 + FORGET_AFTER_S + 40)
    assert [a["hex"] for a in seen] == ["adfdf9"]              # ...so it alerts again


def test_747_200_is_e4b_only_by_tail():
    feed = {"ac": [
        {"hex": "adfeb5", "t": "B742", "r": "74-0787"},     # E-4B
        {"hex": "adfdf8", "t": "B742", "r": "82-8000"},     # VC-25A, not an E-4B
        {"hex": "adfeb6", "t": "B742"},                     # no tail: can't tell, assume E-4B
        {"hex": "adfeb7", "t": "B742", "r": "N12345"},      # some other 747-200
    ]}
    got = {a["hex"]: a["label"] for a in pick(feed, now=0)}
    assert got == {"adfeb5": "E-4B Nightwatch", "adfdf8": "VC-25A Air Force One",
                   "adfeb6": "E-4B Nightwatch"}


def test_bomber_and_recon_types():
    feed = {"ac": [{"hex": "ae0010", "t": "B1", "r": "86-0103"},
                   {"hex": "ae0011", "t": "R135", "r": "62-4128"}]}
    got = {a["hex"]: a["label"] for a in pick(feed, now=0)}
    assert got == {"ae0010": "B-1B Lancer", "ae0011": "RC-135"}


def test_wc135r_by_type_or_tail():
    feed = {"ac": [{"hex": "ae037f", "t": "W135", "r": "64-14829"},
                   {"hex": "ae07c6", "t": "K35R", "r": "64-14831"},     # typed as the tanker it was
                   {"hex": "ae0002", "t": "K35R", "r": "57-1473"}]}     # an actual tanker
    got = {a["hex"]: a["label"] for a in pick(feed, now=0)}
    assert got == {"ae037f": "WC-135R Constant Phoenix", "ae07c6": "WC-135R Constant Phoenix"}


class FakeFeeds(AircraftWatch):
    """AircraftWatch whose fetch() follows a script: url -> list of responses (dict or exception)."""
    def __init__(self, script):
        super().__init__(",".join(script), 30, lambda s: None)
        self.script, self.calls = script, []

    def fetch(self, url):
        self.calls.append(url)
        r = self.script[url].pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def too_many(retry_after=None):
    import urllib.error
    from email.message import Message
    h = Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://a", 429, "Too Many Requests", h, None)


def test_falls_back_and_backs_off_a_failing_feed():
    w = FakeFeeds({"http://a": [too_many(), too_many(), FEED], "http://b": [FEED, FEED, FEED]})
    snap = w.poll(now=1000)
    assert w.calls == ["http://a", "http://b"]
    assert snap["source"] == "http://b" and snap["error"] is None and snap["aircraft"]
    w.poll(now=1010)                              # a rests for 30 s
    assert w.calls[2:] == ["http://b"]
    w.poll(now=1031)                              # a fails again: rests 60 s
    assert w.calls[3:] == ["http://a", "http://b"]
    w.poll(now=1080)
    assert w.calls[5:] == ["http://b"]
    snap = w.poll(now=1092)                       # a is back and preferred again
    assert w.calls[6:] == ["http://a"] and snap["source"] == "http://a"


def test_retry_after_and_all_feeds_down():
    w = FakeFeeds({"http://a": [too_many("600")], "http://b": [OSError("down"), OSError("down")]})
    snap = w.poll(now=1000)
    assert w.calls == ["http://a", "http://b"]
    assert snap["error"] == "down" and snap["updated_at"] is None    # nothing good yet
    w.poll(now=1031)                              # b rested 30 s; a still resting (Retry-After 600)
    assert w.calls[2:] == ["http://b"]
    snap = w.poll(now=1050)                       # both resting: no requests at all
    assert w.calls[3:] == [] and snap["error"] == "every feed is backing off"


def test_approx_position_when_not_broadcasting():
    feed = {"ac": [
        # RC-135S as adsb.fi sent it: no lat/lon, a 19-minute-old lastPosition, and a rough one
        {"hex": "ae01d7", "t": "R135", "r": "62-4128", "alt_baro": 36000, "seen": 0.2,
         "rr_lat": 34.3, "rr_lon": 135.6,
         "lastPosition": {"lat": 33.92817, "lon": 139.081588, "seen_pos": 1161.39}},
        {"hex": "ae6bec", "t": "B1", "r": "86-0103", "rr_lat": 44.7, "rr_lon": -102.4, "seen": 5},
        {"hex": "ae0010", "t": "B1", "r": "86-0099", "rr_lat": 40.0, "rr_lon": -100.0,   # too old
         "lastPosition": {"lat": 41.0, "lon": -99.0, "seen_pos": 7200}},
        {"hex": "ae0011", "t": "B52", "r": "60-0001", "lat": 48.4, "lon": -101.3,          # live
         "rr_lat": 48.0, "rr_lon": -101.0},
        {"hex": "ae0012", "t": "B52", "r": "60-0002"},                                     # nothing
    ]}
    got = {a["hex"]: a for a in pick(feed, now=10000)}
    assert got["ae01d7"]["lat"] is None
    assert got["ae01d7"]["approx"] == {"kind": "last", "lat": 33.92817, "lon": 139.081588,
                                       "at": 10000 - 1161.39}
    assert got["ae6bec"]["approx"] == {"kind": "rough", "lat": 44.7, "lon": -102.4, "at": None}
    assert got["ae0010"]["approx"]["kind"] == "rough"
    assert got["ae0011"]["approx"] is None
    assert got["ae0012"]["approx"] is None


def test_approx_positions_dont_make_trails():
    w = AircraftWatch("http://example.invalid", 30, lambda s: None)
    snap = w.update({"ac": [{"hex": "ae6bec", "t": "B1", "rr_lat": 44.7, "rr_lon": -102.4}]}, now=0)
    assert snap["aircraft"][0]["trail"] == []
