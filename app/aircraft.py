"""Strategic aircraft from public ADS-B: polls an ADSBExchange-v2-style feed.

The default feed is adsb.lol's military list, with adsb.fi's as a fallback. These
aircraft often fly without broadcasting, so an empty map is normal; we only see what
public receivers hear.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger("skywatch.aircraft")

# ICAO type designator -> what we call it
WATCH_TYPES = {
    "E6": "E-6B Mercury",
    "E4B": "E-4B Nightwatch",
    "B52": "B-52H Stratofortress",
    "B2": "B-2 Spirit",
    "B1": "B-1B Lancer",
    "C135": "WC-135 / OC-135",
    "W135": "WC-135R Constant Phoenix",
    "R135": "RC-135",
}
# E-4B airframes, in case the feed types them oddly
WATCH_REGS = {r: "E-4B Nightwatch" for r in ("73-1676", "73-1677", "74-0787", "75-0125")}
# the two "Air Force One" jets, which feeds type as a 747-200 like the E-4B
WATCH_REGS.update({r: "VC-25A Air Force One" for r in ("82-8000", "92-9000")})
# WC-135Rs, converted KC-135R tankers, in case a feed types them K35R
WATCH_REGS.update({r: "WC-135R Constant Phoenix" for r in ("64-14829", "64-14831", "64-14836")})

TRAIL_POINTS = 120          # ~1 h at the default 30 s poll
FORGET_AFTER_S = 15 * 60    # drop an aircraft this long after it was last heard
MAX_BACKOFF_S = 15 * 60     # a failing feed is retried at least this often
LAST_POS_MAX_S = 60 * 60    # older last-known positions aren't worth drawing


def label_for(ac: dict) -> Optional[str]:
    reg = (ac.get("r") or "").strip()
    if reg in WATCH_REGS:
        return WATCH_REGS[reg]
    typ = (ac.get("t") or "").strip().upper()
    # Feeds often type the E-4B as a plain 747-200 (B742), but so are the VC-25A
    # "Air Force One" jets (82-8000, 92-9000). Trust B742 only when there's no tail.
    if typ == "B742":
        return None if reg else "E-4B Nightwatch"
    return WATCH_TYPES.get(typ)


def _callsign(raw: Optional[str]) -> Optional[str]:
    """The flight field, minus placeholders some transponders send ("00000000", "@@@@@@@@")."""
    cs = (raw or "").strip().upper()
    return cs if cs.isalnum() and cs.strip("0") else None


def approx_position(ac: dict, now: float) -> Optional[dict[str, Any]]:
    """Where an aircraft that isn't broadcasting a position probably is.

    "last": its last broadcast position (readsb's lastPosition), if under LAST_POS_MAX_S old.
    "rough": readsb's rr_lat/rr_lon, the location of a receiver hearing it, so only
    within a few hundred km.
    """
    last = ac.get("lastPosition") or {}
    age = last.get("seen_pos")
    if last.get("lat") is not None and last.get("lon") is not None \
            and isinstance(age, (int, float)) and age <= LAST_POS_MAX_S:
        return {"kind": "last", "lat": last["lat"], "lon": last["lon"], "at": now - age}
    if ac.get("rr_lat") is not None and ac.get("rr_lon") is not None:
        return {"kind": "rough", "lat": ac["rr_lat"], "lon": ac["rr_lon"], "at": None}
    return None


def pick(feed: dict, now: float) -> list[dict[str, Any]]:
    """Watched aircraft from one feed response, normalized for the UI."""
    out = []
    for ac in feed.get("ac") or []:
        label = label_for(ac)
        if not label:
            continue
        alt = ac.get("alt_baro")
        seen = ac.get("seen_pos", ac.get("seen"))   # position age, else any-message age
        live = ac.get("lat") is not None and ac.get("lon") is not None
        out.append({
            "hex": ac.get("hex"),
            "label": label,
            "type": ac.get("t"),
            "reg": (ac.get("r") or "").strip() or None,
            "callsign": _callsign(ac.get("flight")),
            "lat": ac.get("lat"),
            "lon": ac.get("lon"),
            "alt": alt if alt == "ground" or isinstance(alt, (int, float)) else None,
            "gs": ac.get("gs"),
            "track": ac.get("track"),
            "squawk": ac.get("squawk"),
            "seen_at": now - seen if isinstance(seen, (int, float)) else now,
            # map hint when there's no live position; lat/lon (and the trail) stay live-only
            "approx": None if live else approx_position(ac, now),
        })
    return out


class AircraftWatch:
    def __init__(self, url: str, poll_s: float, on_update: Callable[[dict], None],
                 on_appear: Optional[Callable[[dict], None]] = None) -> None:
        # comma-separated feeds, in order of preference; a failing feed rests while the next one is used
        self.urls = [u.strip() for u in url.split(",") if u.strip()]
        self.url = self.urls[0]
        self.poll_s = poll_s
        self._fails: dict[str, int] = {}
        self._rest_until: dict[str, float] = {}
        self.on_update = on_update
        self.on_appear = on_appear   # called once per aircraft when it is first heard
        self._known: dict[str, dict] = {}
        self._trails: dict[str, collections.deque] = {}
        self._snapshot: dict[str, Any] = {"updated_at": None, "source": self.url, "error": None,
                                          "aircraft": []}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="aircraft", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot

    def fetch(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={
            "User-Agent": "skywatch (+https://github.com/zkm/skywatch)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    def update(self, feed: dict, now: float) -> dict[str, Any]:
        for ac in pick(feed, now):
            if ac["hex"] not in self._known and self.on_appear:
                self.on_appear(ac)
            self._known[ac["hex"]] = ac
            if ac["lat"] is not None and ac["lon"] is not None:
                trail = self._trails.setdefault(ac["hex"], collections.deque(maxlen=TRAIL_POINTS))
                pt = [round(ac["lat"], 4), round(ac["lon"], 4)]
                if not trail or trail[-1] != pt:
                    trail.append(pt)
        for hx in [h for h, a in self._known.items()
                   if a["seen_at"] < now - FORGET_AFTER_S]:
            self._known.pop(hx)
            self._trails.pop(hx, None)
        aircraft = [{**a, "trail": list(self._trails.get(h, []))} for h, a in self._known.items()]
        return {"updated_at": now, "source": self.url, "error": None, "aircraft": aircraft}

    def _rest(self, url: str, err: Exception, now: float) -> None:
        """Back off a failing feed: doubling from poll_s up to MAX_BACKOFF_S, or longer if it says so."""
        n = self._fails[url] = self._fails.get(url, 0) + 1
        wait = min(MAX_BACKOFF_S, self.poll_s * 2 ** (n - 1))
        if isinstance(err, urllib.error.HTTPError):
            try:
                wait = max(wait, float(err.headers.get("Retry-After", "")))
            except (TypeError, ValueError):   # absent, or an HTTP date
                pass
        self._rest_until[url] = now + wait
        log.warning("ADS-B feed %s failed (%s), resting %.0f s", url, err, wait)

    def poll(self, now: float) -> dict[str, Any]:
        """One poll: the first feed that isn't resting and answers; the last good list otherwise."""
        error = "every feed is backing off"
        for url in self.urls:
            if self._rest_until.get(url, 0) > now:
                continue
            try:
                feed = self.fetch(url)
            except Exception as e:  # network, JSON, upstream hiccup: try the next feed
                self._rest(url, e, now)
                error = str(e)
                continue
            self._fails.pop(url, None)
            self._rest_until.pop(url, None)
            return {**self.update(feed, now), "source": url}
        with self._lock:
            return {**self._snapshot, "error": error}

    def _run(self) -> None:
        while not self._stop.is_set():
            snap = self.poll(time.time())
            with self._lock:
                self._snapshot = snap
            self.on_update(snap)
            self._stop.wait(self.poll_s)
