"""Strategic aircraft from public ADS-B: polls an ADSBExchange-v2-style feed.

The default feed is adsb.lol's military list. These aircraft often fly without
broadcasting, so an empty map is normal; we only see what public receivers hear.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger("skywatch.aircraft")

# ICAO type designator -> what we call it
WATCH_TYPES = {
    "E6": "E-6B Mercury",
    "E4B": "E-4B Nightwatch",
    "B742": "E-4B Nightwatch",   # only in the military feed; civil 747-200s don't appear there
    "B52": "B-52H Stratofortress",
    "B2": "B-2 Spirit",
    "C135": "WC-135 / OC-135",
}
# E-4B airframes, in case the feed types them oddly
WATCH_REGS = {r: "E-4B Nightwatch" for r in ("73-1676", "73-1677", "74-0787", "75-0125")}

TRAIL_POINTS = 120          # ~1 h at the default 30 s poll
FORGET_AFTER_S = 15 * 60    # drop an aircraft this long after it was last heard


def label_for(ac: dict) -> Optional[str]:
    reg = (ac.get("r") or "").strip()
    if reg in WATCH_REGS:
        return WATCH_REGS[reg]
    return WATCH_TYPES.get((ac.get("t") or "").strip().upper())


def pick(feed: dict, now: float) -> list[dict[str, Any]]:
    """Watched aircraft from one feed response, normalized for the UI."""
    out = []
    for ac in feed.get("ac") or []:
        label = label_for(ac)
        if not label:
            continue
        alt = ac.get("alt_baro")
        seen = ac.get("seen_pos", ac.get("seen"))   # position age, else any-message age
        out.append({
            "hex": ac.get("hex"),
            "label": label,
            "type": ac.get("t"),
            "reg": (ac.get("r") or "").strip() or None,
            "callsign": (ac.get("flight") or "").strip() or None,
            "lat": ac.get("lat"),
            "lon": ac.get("lon"),
            "alt": alt if alt == "ground" or isinstance(alt, (int, float)) else None,
            "gs": ac.get("gs"),
            "track": ac.get("track"),
            "squawk": ac.get("squawk"),
            "seen_at": now - seen if isinstance(seen, (int, float)) else now,
        })
    return out


class AircraftWatch:
    def __init__(self, url: str, poll_s: float, on_update: Callable[[dict], None],
                 on_appear: Optional[Callable[[dict], None]] = None) -> None:
        self.url = url
        self.poll_s = poll_s
        self.on_update = on_update
        self.on_appear = on_appear   # called once per aircraft when it is first heard
        self._known: dict[str, dict] = {}
        self._trails: dict[str, collections.deque] = {}
        self._snapshot: dict[str, Any] = {"updated_at": None, "source": url, "error": None,
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

    def fetch(self) -> dict:
        req = urllib.request.Request(self.url, headers={
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

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snap = self.update(self.fetch(), time.time())
            except Exception as e:  # network, JSON, upstream hiccup: keep the last good list
                log.warning("ADS-B poll failed: %s", e)
                with self._lock:
                    snap = {**self._snapshot, "error": str(e)}
            with self._lock:
                self._snapshot = snap
            self.on_update(snap)
            self._stop.wait(self.poll_s)
