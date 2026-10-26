"""SQLite persistence."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from .corrections import Tallies, crowd_copy

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   REAL NOT NULL,          -- epoch seconds, UTC
    duration     REAL NOT NULL,
    kind         TEXT NOT NULL,          -- eam | skyking | voice
    callsign     TEXT,
    header       TEXT,
    body         TEXT,
    copy1        TEXT,
    copy2        TEXT,
    expected_len INTEGER,
    unknowns     INTEGER,                -- count of '?' in body
    chars        TEXT,                   -- JSON [{c,t0,t1,p,alts}]
    skyking      TEXT,                   -- JSON
    transcript   TEXT,
    words        TEXT,                   -- JSON [{text,start,end,prob}]
    audio        TEXT NOT NULL,          -- file name under DATA_DIR/audio
    source       TEXT,
    freq_khz     REAL,
    peak_db      REAL,
    floor_db     REAL
);
CREATE INDEX IF NOT EXISTS messages_kind_time ON messages(kind, started_at DESC);
CREATE TABLE IF NOT EXISTS corrections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    pos        INTEGER NOT NULL,         -- 0-based index into messages.body
    char       TEXT NOT NULL,
    voter      TEXT NOT NULL,            -- salted hash, see corrections.voter_id
    created_at REAL NOT NULL,
    UNIQUE (message_id, pos, voter)      -- re-voting replaces your earlier vote
);
CREATE TABLE IF NOT EXISTS alerts (
    key        TEXT PRIMARY KEY,         -- what was alerted on, e.g. "aircraft:adfeb5"
    sent_at    REAL NOT NULL             -- so restarts don't repeat recent alerts
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint   TEXT PRIMARY KEY,         -- browser push service URL
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

LIST_COLS = ("id, started_at, duration, kind, callsign, header, body, crowd_body, expected_len, "
             "unknowns, skyking, audio, freq_khz")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(messages)")}
        if "crowd_body" not in cols:     # databases created before crowd corrections
            self._db.execute("ALTER TABLE messages ADD COLUMN crowd_body TEXT")
            self._db.commit()
        self._lock = threading.Lock()

    def insert(self, row: dict[str, Any]) -> int:
        row = dict(row)
        for k in ("chars", "skyking", "words"):
            if row.get(k) is not None and not isinstance(row[k], str):
                row[k] = json.dumps(row[k])
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        with self._lock:
            cur = self._db.execute(f"INSERT INTO messages ({cols}) VALUES ({qs})",
                                   tuple(row.values()))
            self._db.commit()
            return int(cur.lastrowid)

    @staticmethod
    def _decode(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        for k in ("chars", "skyking", "words"):
            if d.get(k):
                d[k] = json.loads(d[k])
        return d

    def list(self, kinds: Optional[list[str]] = None, limit: int = 50,
             before: Optional[int] = None) -> list[dict[str, Any]]:
        where, args = [], []
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        if before:
            where.append("id < ?")
            args.append(before)
        sql = f"SELECT {LIST_COLS} FROM messages"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(min(max(limit, 1), 200))
        with self._lock:
            return [self._decode(r) for r in self._db.execute(sql, args)]

    def get(self, msg_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            r = self._db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
            if not r:
                return None
            d = self._decode(r)
            d["corrections"] = self._tallies(msg_id)
        return d

    def _tallies(self, msg_id: int) -> Tallies:
        out: Tallies = {}
        for r in self._db.execute(
                "SELECT pos, char, COUNT(*) AS n FROM corrections WHERE message_id = ? "
                "GROUP BY pos, char", (msg_id,)):
            out.setdefault(r["pos"], {})[r["char"]] = r["n"]
        return out

    def correct(self, msg_id: int, pos: int, char: str, voter: str, min_votes: int,
                now: float) -> dict[str, Any]:
        """Record (or replace) one voter's call for one position; returns the new state."""
        with self._lock:
            body = self._db.execute("SELECT body FROM messages WHERE id = ?",
                                    (msg_id,)).fetchone()["body"]
            self._db.execute(
                "INSERT INTO corrections (message_id, pos, char, voter, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (message_id, pos, voter) "
                "DO UPDATE SET char = excluded.char, created_at = excluded.created_at",
                (msg_id, pos, char, voter, now))
            tallies = self._tallies(msg_id)
            crowd = crowd_copy(body, tallies, min_votes)
            self._db.execute("UPDATE messages SET crowd_body = ? WHERE id = ?", (crowd, msg_id))
            self._db.commit()
        return {"id": msg_id, "pos": pos, "corrections": tallies, "crowd_body": crowd}

    def add_push_subscription(self, sub: dict[str, str], now: float) -> bool:
        """Insert or refresh; True when the endpoint is new."""
        with self._lock:
            new = self._db.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = ?",
                                   (sub["endpoint"],)).fetchone() is None
            self._db.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth",
                (sub["endpoint"], sub["p256dh"], sub["auth"], now))
            self._db.commit()
        return new

    def remove_push_subscription(self, endpoint: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
            self._db.commit()

    def push_subscriptions(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT endpoint, p256dh, auth FROM push_subscriptions")]

    def claim_alert(self, key: str, now: float, quiet_s: float) -> bool:
        """True (and records it) unless `key` was already alerted within `quiet_s`."""
        with self._lock:
            r = self._db.execute("SELECT sent_at FROM alerts WHERE key = ?", (key,)).fetchone()
            if r and r["sent_at"] > now - quiet_s:
                return False
            self._db.execute("INSERT INTO alerts (key, sent_at) VALUES (?, ?) ON CONFLICT (key) "
                             "DO UPDATE SET sent_at = excluded.sent_at", (key, now))
            self._db.commit()
        return True

    def release_alert(self, key: str) -> None:
        """Forget a claimed alert that couldn't be sent, so the next sighting retries."""
        with self._lock:
            self._db.execute("DELETE FROM alerts WHERE key = ?", (key,))
            self._db.commit()

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {r["kind"]: r["n"] for r in
                    self._db.execute("SELECT kind, COUNT(*) AS n FROM messages GROUP BY kind")}
