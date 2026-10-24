import sqlite3

from app.corrections import RateLimiter, consensus, crowd_copy, voter_id
from app.store import Store


def test_consensus_needs_min_votes_and_no_tie():
    assert consensus({"9": 2}, 2) == "9"
    assert consensus({"9": 1}, 2) is None
    assert consensus({"9": 2, "G": 2}, 2) is None
    assert consensus({"9": 3, "G": 2}, 2) == "9"
    assert consensus({}, 1) is None


def test_crowd_copy_replaces_agreed_positions_only():
    body = "AB?D?F"
    tallies = {2: {"C": 2}, 4: {"E": 1}}
    assert crowd_copy(body, tallies, 2) == "ABCD?F"
    assert crowd_copy(body, {4: {"E": 1}}, 2) is None       # nothing changed
    assert crowd_copy(body, {0: {"A": 5}}, 2) is None       # agrees with machine


def test_rate_limiter_window():
    rl = RateLimiter(2, 60)
    assert rl.allow("v", now=0) and rl.allow("v", now=1)
    assert not rl.allow("v", now=2)
    assert rl.allow("other", now=2)
    assert rl.allow("v", now=61)


def test_voter_id_is_stable_and_salted():
    assert voter_id(b"s", "1.2.3.4") == voter_id(b"s", "1.2.3.4")
    assert voter_id(b"s", "1.2.3.4") != voter_id(b"t", "1.2.3.4")


def _eam(store):
    return store.insert({"started_at": 0, "duration": 1, "kind": "eam", "body": "AB?D",
                         "audio": "x.mp3"})


def test_store_votes_replace_and_reach_consensus(tmp_path):
    s = Store(tmp_path / "db.sqlite3")
    mid = _eam(s)
    r = s.correct(mid, 2, "X", "alice", 2, now=1)
    assert r["corrections"] == {2: {"X": 1}} and r["crowd_body"] is None
    r = s.correct(mid, 2, "C", "alice", 2, now=2)            # alice changes her mind
    assert r["corrections"] == {2: {"C": 1}}
    r = s.correct(mid, 2, "C", "bob", 2, now=3)
    assert r["crowd_body"] == "ABCD"
    assert s.get(mid)["corrections"] == {2: {"C": 2}}
    assert s.list(["eam"])[0]["crowd_body"] == "ABCD"


def test_store_migrates_old_database(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, started_at REAL NOT NULL, "
               "duration REAL NOT NULL, kind TEXT NOT NULL, callsign TEXT, header TEXT, body TEXT, "
               "copy1 TEXT, copy2 TEXT, expected_len INTEGER, unknowns INTEGER, chars TEXT, "
               "skyking TEXT, transcript TEXT, words TEXT, audio TEXT NOT NULL, source TEXT, "
               "freq_khz REAL, peak_db REAL, floor_db REAL)")
    db.commit()
    db.close()
    s = Store(path)
    mid = _eam(s)
    assert s.list()[0]["crowd_body"] is None
    assert s.correct(mid, 2, "C", "a", 1, now=0)["crowd_body"] == "ABCD"
