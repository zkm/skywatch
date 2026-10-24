"""Notifications for new messages: browser Web Push (VAPID) and an optional ntfy topic.

pywebpush is imported lazily so the rest of the app (and the tests) run without it.
Sends happen on a small thread pool so a slow push service never holds up ASR.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("skywatch.push")

TTL_S = 6 * 3600   # how long a push service holds a message for an offline device
MAX_FIELD = 512    # subscription fields are short; reject anything silly


def describe(m: dict[str, Any]) -> dict[str, Any]:
    """Notification payload for one stored message summary."""
    when = datetime.fromtimestamp(m["started_at"], timezone.utc).strftime("%H:%MZ")
    who = f" from {m['callsign']}" if m.get("callsign") else ""
    if m["kind"] == "eam":
        body = m.get("crowd_body") or m.get("body") or ""
        unknown = body.count("?")
        extra = f", {unknown} unknown" if unknown else ""
        title = "New EAM copied"
        text = f"{body}\n{len(body)} characters{who} at {when}{extra}"
    elif m["kind"] == "skyking":
        k = m.get("skyking") or {}
        title = "Skyking"
        text = f"{k.get('codeword') or '?'}, time {k.get('time') or '??'}, auth {k.get('authentication') or '??'}{who} at {when}"
    else:
        title = "HFGCS traffic"
        text = f"Voice traffic{who} at {when}"
    return {"title": title, "body": text, "id": m["id"], "kind": m["kind"], "url": "./#monitor"}


def _b64url_len(s: str) -> int:
    try:
        return len(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    except ValueError:
        return -1


def valid_subscription(sub: Any) -> Optional[dict[str, str]]:
    """The fields we keep from a browser PushSubscription.toJSON(), or None."""
    if not isinstance(sub, dict):
        return None
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not all(isinstance(v, str) and 0 < len(v) <= MAX_FIELD for v in (endpoint, p256dh, auth)):
        return None
    if not endpoint.startswith("https://"):
        return None
    # uncompressed P-256 public key and a 16-byte auth secret, as browsers send them
    if _b64url_len(p256dh) != 65 or _b64url_len(auth) != 16:
        return None
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


class Notifier:
    def __init__(self, store, kinds: set[str], vapid_path: Path, subject: str,
                 ntfy_url: str = "") -> None:
        self.store = store
        self.kinds = kinds
        self.subject = subject
        self.ntfy_url = ntfy_url
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="push")
        self._vapid = None
        self.public_key: Optional[str] = None
        if not subject:
            log.info("browser push off: set PUSH_SUBJECT=mailto:you@example.com to enable it")
            return
        try:
            from py_vapid import _check_sub
            if not _check_sub(subject):
                raise SystemExit(f"PUSH_SUBJECT must be mailto:you@example.com or https://host "
                                 f"(no path), got {subject!r}")
            self._vapid, self.public_key = self._load_vapid(vapid_path)
        except ImportError:
            log.warning("pywebpush not installed; browser push disabled")

    @staticmethod
    def _load_vapid(path: Path):
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid02
        if path.exists():
            v = Vapid02.from_file(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            v = Vapid02()
            v.generate_keys()
            v.save_key(str(path))
            log.info("generated VAPID key at %s", path)
        raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
        return v, base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    @property
    def enabled(self) -> bool:
        return self._vapid is not None

    def message(self, m: dict[str, Any]) -> None:
        """Called from the pipeline thread for every stored message."""
        if m["kind"] not in self.kinds:
            return
        payload = describe(m)
        if self.enabled:
            for sub in self.store.push_subscriptions():
                self._pool.submit(self._send, sub, payload)
        if self.ntfy_url:
            self._pool.submit(self._ntfy, payload)

    def confirm(self, sub: dict[str, str]) -> None:
        """First push right after subscribing, so people know it works."""
        self._pool.submit(self._send, sub, {
            "title": "Skywatch notifications are on",
            "body": "You'll get one of these for each new Emergency Action Message.",
            "id": 0, "kind": "test", "url": "./#monitor"})

    def _send(self, sub: dict[str, str], payload: dict) -> None:
        from pywebpush import WebPushException, webpush
        info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
        try:
            webpush(info, json.dumps(payload), vapid_private_key=self._vapid,
                    vapid_claims={"sub": self.subject}, ttl=TTL_S, timeout=15,
                    headers={"Urgency": "high"})
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):          # browser unsubscribed or the subscription expired
                self.store.remove_push_subscription(sub["endpoint"])
                log.info("dropped expired push subscription")
            else:
                log.warning("push failed (%s): %s", code, e)
        except Exception as e:
            log.warning("push failed: %s", e)

    def _ntfy(self, payload: dict) -> None:
        req = urllib.request.Request(
            self.ntfy_url, data=payload["body"].encode(), method="POST",
            headers={"Title": payload["title"], "Tags": "radio", "Priority": "high"})
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            log.warning("ntfy failed: %s", e)
