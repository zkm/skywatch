"""Energy squelch that cuts the continuous HF stream into transmissions.

HFGCS is SSB voice over a steady noise floor, so a relative-energy squelch
works well: track the noise floor while closed, open when short-term energy
rises OPEN_DB above it, close after HANG_S below CLOSE_DB. Hysteresis plus a
long hang keeps an EAM (with its pauses between character groups) in one piece.

The floor is frozen while open, so if the background noise rises mid-transmission
the level never falls back below it. A transmission that runs to MAX_TX_S is
taken as a sign of that: the floor is re-measured from the last few seconds
instead of reopening straight away on the same noise.
"""
from __future__ import annotations

import collections
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

FRAME_MS = 20
RESEED_FRAMES = 500         # 10 s of recent levels to re-measure a stale floor from


@dataclass
class Transmission:
    started_at: float          # epoch seconds of first sample (incl. preroll)
    samples: np.ndarray        # int16 mono
    sample_rate: int
    peak_db: float
    floor_db: float

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate


class Segmenter:
    def __init__(self, sample_rate: int, open_db: float, close_db: float, hang_s: float,
                 preroll_s: float, min_tx_s: float, max_tx_s: float,
                 on_transmission: Callable[[Transmission], None],
                 clock: Callable[[], float] = time.time) -> None:
        self.sr = sample_rate
        self.frame = sample_rate * FRAME_MS // 1000
        self.open_db, self.close_db = open_db, close_db
        self.hang_frames = int(hang_s * 1000 / FRAME_MS)
        self.min_frames = int(min_tx_s * 1000 / FRAME_MS)
        self.max_frames = int(max_tx_s * 1000 / FRAME_MS)
        self.on_tx = on_transmission
        self.clock = clock

        self._pending = np.zeros(0, dtype=np.int16)
        self._preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=max(1, int(preroll_s * 1000 / FRAME_MS)))
        self._recent_db: collections.deque[float] = collections.deque(maxlen=10)  # 200 ms
        self._warmup: list[float] = []
        self.floor_db: Optional[float] = None
        self.level_db: float = -120.0
        self.is_open = False
        self._frames: list[np.ndarray] = []
        self._open_db: collections.deque[float] = collections.deque(maxlen=RESEED_FRAMES)
        self._below = 0
        self._peak = -120.0
        self._t0 = 0.0

    @staticmethod
    def _db(frame: np.ndarray) -> float:
        rms = np.sqrt(np.mean(frame.astype(np.float64) ** 2))
        return 20.0 * np.log10(max(rms, 1e-3) / 32768.0)

    def feed(self, pcm: bytes) -> None:
        data = np.frombuffer(pcm, dtype=np.int16)
        if self._pending.size:
            data = np.concatenate([self._pending, data])
        n = (len(data) // self.frame) * self.frame
        self._pending = data[n:].copy()
        for i in range(0, n, self.frame):
            self._frame(data[i:i + self.frame])

    def _frame(self, frame: np.ndarray) -> None:
        db = self._db(frame)
        self._recent_db.append(db)
        level = float(np.mean(self._recent_db))
        self.level_db = level

        if self.floor_db is None:          # ~1 s warm-up to seed the floor
            self._warmup.append(db)
            self._preroll.append(frame)
            if len(self._warmup) >= 50:
                self.floor_db = float(np.median(self._warmup))
            return

        if not self.is_open:
            # fast attack downward, slow drift upward (~40 s time constant)
            if db < self.floor_db:
                self.floor_db += (db - self.floor_db) * 0.05
            else:
                self.floor_db += (db - self.floor_db) * 0.0005
            if level > self.floor_db + self.open_db:
                self._open(frame)
            else:
                self._preroll.append(frame)
            return

        self._frames.append(frame)
        self._open_db.append(db)
        self._peak = max(self._peak, level)
        if level < self.floor_db + self.close_db:
            self._below += 1
        else:
            self._below = 0
        if self._below >= self.hang_frames:
            self._close()
        elif len(self._frames) >= self.max_frames:
            self._close()
            # ran to the limit: the floor is probably stale, so re-measure it
            self.floor_db = float(np.median(self._open_db))

    def _open(self, frame: np.ndarray) -> None:
        self.is_open = True
        pre = list(self._preroll)
        self._preroll.clear()
        self._frames = pre + [frame]
        self._t0 = self.clock() - len(self._frames) * FRAME_MS / 1000.0
        self._below = 0
        self._open_db.clear()
        self._peak = self.level_db

    def _close(self) -> None:
        self.is_open = False
        frames = self._frames
        # trim most of the trailing hang, keep ~0.5 s of tail
        keep_tail = 25
        if self._below > keep_tail:
            frames = frames[: len(frames) - (self._below - keep_tail)]
        self._frames = []
        self._below = 0
        if len(frames) < self.min_frames:
            return
        self.on_tx(Transmission(
            started_at=self._t0,
            samples=np.concatenate(frames),
            sample_rate=self.sr,
            peak_db=self._peak,
            floor_db=float(self.floor_db or -120.0),
        ))

    def flush(self) -> None:
        if self.is_open:
            self._below = 0
            self._close()
