import base64

from app.push import Mailer, Notifier, describe, describe_aircraft, valid_subscription
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


E4B = {"hex": "adfeb5", "label": "E-4B Nightwatch", "type": "B742", "reg": "74-0787",
       "callsign": "GORDO21", "lat": 40.6331, "lon": -88.5770, "alt": 24000, "gs": 253.4,
       "track": 274.0, "squawk": "6703", "seen_at": 1792800000}


def test_describe_aircraft():
    subject, body = describe_aircraft(E4B)
    assert subject == "E-4B Nightwatch 74-0787 is up"
    assert "at 2026-10-24 00:00Z." in body
    assert "Altitude:  24,000 ft" in body and "Speed:     253 kt" in body
    assert "Position:  40.633, -88.577" in body
    assert body.endswith("https://globe.adsb.lol/?icao=adfeb5")
    _, body = describe_aircraft({**E4B, "lat": None, "lon": None, "alt": "ground", "gs": None,
                                 "reg": None, "callsign": None})
    assert "Position:  not broadcast" in body and "Altitude:  on the ground" in body
    assert "Speed:     -" in body


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout):
        self.tls = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return name == "starttls"

    def starttls(self, context):
        self.tls = True

    def login(self, user, password):
        assert self.tls, "logged in before STARTTLS"

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_notifier_emails_only_for_e4b(tmp_path, monkeypatch):
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    FakeSMTP.sent = []
    mailer = Mailer("smtp.example", 587, "me@example.com", "pw", "", ["a@example.com", "b@example.com"])
    n = Notifier(Store(tmp_path / "db.sqlite3"), {"eam"}, tmp_path / "vapid.pem", "", mailer=mailer)
    n.aircraft(E4B)
    n.aircraft({**E4B, "label": "B-52H Stratofortress"})
    n._pool.shutdown(wait=True)
    assert len(FakeSMTP.sent) == 1
    msg = FakeSMTP.sent[0]
    assert msg["Subject"] == "E-4B Nightwatch 74-0787 is up"
    assert msg["From"] == "me@example.com" and msg["To"] == "a@example.com, b@example.com"


def test_alert_not_repeated_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    FakeSMTP.sent = []
    db = tmp_path / "db.sqlite3"
    mailer = Mailer("smtp.example", 587, "me@example.com", "pw", "", ["a@example.com"])
    for _ in range(2):                  # same aircraft, server restarted in between
        n = Notifier(Store(db), {"eam"}, tmp_path / "vapid.pem", "", mailer=mailer)
        n.aircraft(E4B)
        n._pool.shutdown(wait=True)
    assert len(FakeSMTP.sent) == 1
    n = Notifier(Store(db), {"eam"}, tmp_path / "vapid.pem", "", mailer=mailer)
    n.aircraft({**E4B, "hex": "adfeb6", "reg": "75-0125"})   # a different E-4B still alerts
    n._pool.shutdown(wait=True)
    assert len(FakeSMTP.sent) == 2


def test_failed_email_is_retried_on_next_sighting(tmp_path, monkeypatch):
    class Down(FakeSMTP):
        def send_message(self, msg):
            raise OSError("connection reset")
    monkeypatch.setattr("smtplib.SMTP", Down)
    n = Notifier(Store(tmp_path / "db.sqlite3"), {"eam"}, tmp_path / "vapid.pem", "",
                 mailer=Mailer("smtp.example", 587, "me@example.com", "pw", "", ["a@example.com"]))
    n.aircraft(E4B)
    n._pool.shutdown(wait=True)
    assert n.store.claim_alert("aircraft:adfeb5", 0, 0)    # released, not stuck for 6 h


def test_claim_alert_quiet_window(tmp_path):
    s = Store(tmp_path / "db.sqlite3")
    assert s.claim_alert("aircraft:x", 1000, 3600)
    assert not s.claim_alert("aircraft:x", 1000 + 3599, 3600)
    assert s.claim_alert("aircraft:x", 1000 + 3601, 3600)


def test_mailer_off_without_recipients(tmp_path):
    n = Notifier(Store(tmp_path / "db.sqlite3"), {"eam"}, tmp_path / "vapid.pem", "",
                 mailer=Mailer("smtp.example", 587, "me@example.com", "pw", "", []))
    assert n.mailer is None
    n.aircraft(E4B)     # must not raise
