import base64

from app.push import Notifier, describe, valid_subscription
from app.store import Store

EAM = {"id": 3, "kind": "eam", "started_at": 1792800000, "callsign": "Andrews",
       "body": "AB?D", "crowd_body": None}


def test_describe_eam_uses_crowd_copy_and_counts_unknowns():
    p = describe(EAM)
    assert p["title"] == "New EAM copied" and p["id"] == 3
    assert p["body"].startswith("AB?D\n4 characters from Andrews at ")
    assert p["body"].endswith(", 1 unknown")
    assert describe({**EAM, "crowd_body": "ABCD"})["body"].startswith("ABCD\n")


def test_describe_skyking():
    p = describe({"id": 4, "kind": "skyking", "started_at": 0, "callsign": None,
                  "skyking": {"codeword": "IRON MAIDEN", "time": "25", "authentication": "PS"}})
    assert p["title"] == "Skyking" and p["body"].startswith("IRON MAIDEN, time 25, auth PS at 00:00Z")


B64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
P256 = B64(b"\x04" + bytes(64))
AUTH = B64(bytes(16))


def test_valid_subscription():
    good = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": P256, "auth": AUTH},
            "expirationTime": None}
    assert valid_subscription(good) == {"endpoint": good["endpoint"], "p256dh": P256, "auth": AUTH}
    assert valid_subscription({**good, "keys": {"p256dh": "BPfake", "auth": AUTH}}) is None
    assert valid_subscription({**good, "keys": {"p256dh": P256, "auth": "!!"}}) is None
    assert valid_subscription({**good, "endpoint": "http://insecure/x"}) is None
    assert valid_subscription({**good, "keys": {"p256dh": P256}}) is None
    assert valid_subscription({**good, "endpoint": "https://" + "a" * 600}) is None
    assert valid_subscription(["not", "a", "dict"]) is None


def test_store_subscriptions_upsert_and_remove(tmp_path):
    s = Store(tmp_path / "db.sqlite3")
    sub = {"endpoint": "https://push.example/1", "p256dh": "k1", "auth": "a1"}
    assert s.add_push_subscription(sub, 0) is True
    assert s.add_push_subscription({**sub, "p256dh": "k2"}, 1) is False   # browser rotated keys
    assert s.push_subscriptions() == [{**sub, "p256dh": "k2"}]
    s.remove_push_subscription(sub["endpoint"])
    assert s.push_subscriptions() == []


def test_push_stays_off_without_subject(tmp_path):
    n = Notifier(Store(tmp_path / "db.sqlite3"), {"eam"}, tmp_path / "vapid.pem", "")
    assert not n.enabled and n.public_key is None
    assert not (tmp_path / "vapid.pem").exists()
    n.message(EAM)      # no subscribers, no ntfy: nothing to do, must not raise
