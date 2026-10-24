"""Glue: source -> (live encoder, segmenter) -> transcription worker -> store/events."""
from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone

import numpy as np

from .config import Config
from .live import EventBus, LiveEncoder
from .phonetic import parse
from .segmenter import Segmenter, Transmission
from .source import SourceState, make_source
from .store import Store
from .transcriber import make_transcriber

log = logging.getLogger("skywatch.pipeline")


def encode_mp3(samples: np.ndarray, sr: int, path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "-",
         "-c:a", "libmp3lame", "-b:a", "48k", str(path)],
        input=samples.tobytes(), check=True)


class Pipeline:
    def __init__(self, cfg: Config, store: Store, events: EventBus, live: LiveEncoder) -> None:
        self.cfg = cfg
        self.store = store
        self.events = events
        self.live = live
        self.state = SourceState()
        self.source = make_source(cfg, self.state)
        self.segmenter = Segmenter(
            cfg.sample_rate, cfg.open_db, cfg.close_db, cfg.hang_s, cfg.preroll_s,
            cfg.min_tx_s, cfg.max_tx_s, on_transmission=self._enqueue)
        self.queue: queue.Queue[Transmission] = queue.Queue(maxsize=50)
        self.transcriber = None
        self.busy = False
        cfg.audio_dir.mkdir(parents=True, exist_ok=True)

    # --- threads ---
    def start(self) -> None:
        threading.Thread(target=self._ingest, daemon=True, name="ingest").start()
        threading.Thread(target=self._worker, daemon=True, name="asr").start()
        threading.Thread(target=self._meter, daemon=True, name="meter").start()

    def stop(self) -> None:
        self.source.stop()

    def _ingest(self) -> None:
        for pcm in self.source.chunks():
            self.live.write(pcm)
            self.segmenter.feed(pcm)
        self.segmenter.flush()
        log.info("source ended")

    def _enqueue(self, tx: Transmission) -> None:
        log.info("transmission: %.1fs, peak %.1f dB over floor %.1f dB",
                 tx.duration, tx.peak_db, tx.floor_db)
        self.events.emit("transmission", {"started_at": tx.started_at,
                                          "duration": tx.duration})
        try:
            self.queue.put_nowait(tx)
        except queue.Full:
            log.error("ASR queue full; dropping %.1fs transmission", tx.duration)

    def _meter(self) -> None:
        while True:
            time.sleep(0.5)
            self.events.emit("status", self.status())

    def status(self) -> dict:
        s = self.segmenter
        return {
            "connected": self.state.connected,
            "host": self.state.host,
            "since": self.state.since,
            "last_error": self.state.last_error,
            "finished": self.state.finished,
            "freq_khz": self.cfg.freq_khz,
            "mode": self.cfg.mode,
            "level_db": round(s.level_db, 1),
            "floor_db": round(s.floor_db, 1) if s.floor_db is not None else None,
            "open_db": self.cfg.open_db,
            "squelch_open": s.is_open,
            "asr_busy": self.busy,
            "asr_queue": self.queue.qsize(),
            "listeners": self.live.fanout.count,
        }

    def _worker(self) -> None:
        self.transcriber = make_transcriber(self.cfg)
        while True:
            tx = self.queue.get()
            self.busy = True
            try:
                self._process(tx)
            except Exception:
                log.exception("failed to process transmission")
            finally:
                self.busy = False

    def _process(self, tx: Transmission) -> None:
        assert self.transcriber is not None
        audio = tx.samples.astype(np.float32) / 32768.0
        t0 = time.time()
        text, words = self.transcriber.transcribe(audio)
        log.info("ASR %.1fs audio in %.1fs: %s", tx.duration, time.time() - t0, text[:160])
        parsed = parse(words, self.cfg.min_word_prob)
        if parsed.kind == "voice" and not self.cfg.keep_non_eam:
            return
        if parsed.kind == "voice" and not text and self.cfg.asr_backend != "none":
            return  # ASR heard nothing: squelch opened on noise/QRM

        stamp = datetime.fromtimestamp(tx.started_at, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fname = f"{stamp}_{parsed.kind}.mp3"
        encode_mp3(tx.samples, tx.sample_rate, self.cfg.audio_dir / fname)

        row = {
            "started_at": tx.started_at,
            "duration": round(tx.duration, 2),
            "kind": parsed.kind,
            "callsign": parsed.callsign,
            "header": parsed.header or None,
            "body": parsed.body or None,
            "copy1": parsed.copy1 or None,
            "copy2": parsed.copy2 or None,
            "expected_len": parsed.expected_len,
            "unknowns": parsed.body.count("?") if parsed.body else None,
            "chars": [c.__dict__ for c in parsed.chars] or None,
            "skyking": parsed.skyking,
            "transcript": text or None,
            "words": [w.__dict__ for w in words] or None,
            "audio": fname,
            "source": self.state.host,
            "freq_khz": self.cfg.freq_khz,
            "peak_db": round(tx.peak_db, 1),
            "floor_db": round(tx.floor_db, 1),
        }
        msg_id = self.store.insert(row)
        log.info("stored #%d %s %s", msg_id, parsed.kind, parsed.body or "")
        summary = self.store.list(limit=1)[0]
        self.events.emit("message", summary)
