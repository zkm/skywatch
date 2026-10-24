"""Live MP3 broadcast and server-sent-event fan-out."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
import time
from typing import Any

log = logging.getLogger("skywatch.live")


class Fanout:
    """Thread-safe publish into per-client asyncio queues; slow clients drop data."""

    def __init__(self, maxsize: int = 64) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.maxsize = maxsize

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(self.maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    @property
    def count(self) -> int:
        return len(self._subs)

    def publish(self, item: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            loop.call_soon_threadsafe(self._put, q, item)

    @staticmethod
    def _put(q: asyncio.Queue, item: Any) -> None:
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(item)


class LiveEncoder:
    """One shared ffmpeg MP3 encoder; PCM in, MP3 bytes fanned out to listeners.

    Feeds silence when the source is down so connected players don't stall out.
    """

    def __init__(self, sample_rate: int, bitrate: str) -> None:
        self.sr = sample_rate
        self.fanout = Fanout(maxsize=128)
        self._proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "-",
             "-c:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3",
             "-flush_packets", "1", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._wlock = threading.Lock()
        self._last_write = time.monotonic()
        self._stop = threading.Event()
        threading.Thread(target=self._reader, daemon=True, name="live-reader").start()
        threading.Thread(target=self._filler, daemon=True, name="live-filler").start()

    def write(self, pcm: bytes) -> None:
        with self._wlock:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(pcm)
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                log.error("live encoder pipe closed")
            self._last_write = time.monotonic()

    def _filler(self) -> None:
        silence = b"\x00\x00" * (self.sr // 10)
        while not self._stop.wait(0.1):
            if time.monotonic() - self._last_write > 1.0:
                self.write(silence)

    def _reader(self) -> None:
        assert self._proc.stdout is not None
        while True:
            buf = self._proc.stdout.read1(4096)  # type: ignore[attr-defined]
            if not buf:
                break
            self.fanout.publish(buf)

    def close(self) -> None:
        self._stop.set()
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.terminate()


class EventBus(Fanout):
    def emit(self, event: str, data: dict) -> None:
        self.publish(f"event: {event}\ndata: {json.dumps(data)}\n\n")
