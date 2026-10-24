"""Crowd corrections: listeners vote on what a character really was.

A position takes the crowd's character once at least `min_votes` voters agree
and nobody ties them. Below that the machine copy stands, and the votes are
still shown so the next listener can confirm or dispute them.
"""
from __future__ import annotations

import collections
import hashlib
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

VALID_CHAR = re.compile(r"^[A-Z0-9]$")

Tallies = dict[int, dict[str, int]]   # position -> {char: votes}


def consensus(votes: dict[str, int], min_votes: int) -> Optional[str]:
    if not votes:
        return None
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    char, n = ranked[0]
    if n < min_votes or (len(ranked) > 1 and ranked[1][1] == n):
        return None
    return char


def crowd_copy(body: str, tallies: Tallies, min_votes: int) -> Optional[str]:
    """Body with agreed positions replaced, or None when the crowd changed nothing."""
    out = list(body)
    for pos, votes in tallies.items():
        c = consensus(votes, min_votes)
        if c is not None and 0 <= pos < len(out):
            out[pos] = c
    s = "".join(out)
    return s if s != body else None


def load_salt(path: Path) -> bytes:
    """Per-install secret so voter ids are stable across restarts but not reversible to IPs."""
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(32)
    path.write_bytes(salt)
    return salt


def voter_id(salt: bytes, ip: str) -> str:
    return hashlib.sha256(salt + ip.encode()).hexdigest()[:16]


class RateLimiter:
    """Sliding window, per key, in memory."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True
