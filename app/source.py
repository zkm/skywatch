"""Audio sources. Each yields 16 kHz mono int16 PCM as bytes.

KiwiSource: kiwirecorder.py --nc --nc-wav (WAV stream on stdout, native Kiwi
rate) piped into ffmpeg, which resamples to 16 kHz s16le. Rotates through
KIWI_HOSTS on disconnect (slot full, time limit, network drop).

FileSource: decodes any file ffmpeg can read, paced in real time, for testing.
"""
from __future__ import annotations

import logging
import select
import shlex
import subprocess
import sys
import threading
import time
from typing import Iterator, Optional

from .config import Config

log = logging.getLogger("skywatch.source")

CHUNK_SAMPLES = 1600  # 100 ms at 16 kHz
CHUNK_BYTES = CHUNK_SAMPLES * 2
STALL_TIMEOUT_S = 45


class SourceState:
    def __init__(self) -> None:
        self.connected = False
        self.host: Optional[str] = None
        self.since: Optional[float] = None
        self.last_error: Optional[str] = None
        self.finished = False   # file source played to the end and won't loop


def _ffmpeg_resample_cmd(rate: int, input_args: list[str]) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", *input_args,
            "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]


def _drain_stderr(proc: subprocess.Popen, name: str) -> None:
    assert proc.stderr is not None
    for line in iter(proc.stderr.readline, b""):
        text = line.decode(errors="replace").strip()
        if text:
            log.debug("%s: %s", name, text[-300:])


class KiwiSource:
    def __init__(self, cfg: Config, state: SourceState) -> None:
        if not cfg.kiwi_hosts:
            raise SystemExit("KIWI_HOSTS is empty; set e.g. KIWI_HOSTS=kiwi.example.org:8073")
        self.cfg = cfg
        self.state = state
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _kiwi_cmd(self, host: str, port: int) -> list[str]:
        c = self.cfg
        cmd = [sys.executable, c.kiwirecorder, "--nc", "--nc-wav",
               "-s", host, "-p", str(port),
               "-f", f"{c.freq_khz:g}", "-m", c.mode,
               "-L", str(c.passband_lo), "-H", str(c.passband_hi),
               "-u", c.kiwi_user, "--quiet",
               # give up on a dead/busy Kiwi so we rotate to the next host
               "--connect-retries", "2", "--busy-retries", "2"]
        if c.kiwi_password:
            cmd += ["--pw", c.kiwi_password]
        return cmd

    def chunks(self) -> Iterator[bytes]:
        idx = 0
        hosts = self.cfg.kiwi_hosts
        while not self._stop.is_set():
            host, port = hosts[idx % len(hosts)]
            idx += 1
            kiwi_cmd = self._kiwi_cmd(host, port)
            log.info("connecting: %s", shlex.join(kiwi_cmd))
            kiwi = subprocess.Popen(kiwi_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ff = subprocess.Popen(
                _ffmpeg_resample_cmd(self.cfg.sample_rate, ["-f", "wav", "-i", "-"]),
                stdin=kiwi.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert kiwi.stdout is not None and ff.stdout is not None
            kiwi.stdout.close()  # ffmpeg owns the read end now
            threading.Thread(target=_drain_stderr, args=(kiwi, "kiwi"), daemon=True).start()
            threading.Thread(target=_drain_stderr, args=(ff, "ffmpeg"), daemon=True).start()
            got_audio = False
            fd = ff.stdout
            try:
                while not self._stop.is_set():
                    # watchdog: a Kiwi can stay connected but stop sending audio
                    ready, _, _ = select.select([fd], [], [], STALL_TIMEOUT_S)
                    if not ready:
                        log.warning("no audio from %s:%d for %ds", host, port, STALL_TIMEOUT_S)
                        break
                    buf = fd.read1(CHUNK_BYTES)  # type: ignore[attr-defined]
                    if not buf:
                        break
                    if not got_audio:
                        got_audio = True
                        self.state.connected = True
                        self.state.host = f"{host}:{port}"
                        self.state.since = time.time()
                        self.state.last_error = None
                        log.info("audio flowing from %s:%d", host, port)
                    yield buf
            finally:
                for p in (kiwi, ff):
                    if p.poll() is None:
                        p.terminate()
                for p in (kiwi, ff):
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                self.state.connected = False
            if self._stop.is_set():
                return
            self.state.last_error = f"{host}:{port} disconnected (rc={kiwi.returncode})"
            log.warning("%s; retrying in %.0fs", self.state.last_error, self.cfg.reconnect_backoff_s)
            self._stop.wait(self.cfg.reconnect_backoff_s)


class FileSource:
    def __init__(self, cfg: Config, state: SourceState) -> None:
        if not cfg.file_path:
            raise SystemExit("EAM_SOURCE=file requires EAM_FILE")
        self.cfg = cfg
        self.state = state
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def chunks(self) -> Iterator[bytes]:
        while not self._stop.is_set():
            ff = subprocess.Popen(
                _ffmpeg_resample_cmd(self.cfg.sample_rate, ["-re", "-i", self.cfg.file_path]),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert ff.stdout is not None
            self.state.connected = True
            self.state.host = f"file:{self.cfg.file_path}"
            self.state.since = time.time()
            try:
                while not self._stop.is_set():
                    buf = ff.stdout.read(CHUNK_BYTES)
                    if not buf:
                        break
                    yield buf
            finally:
                if ff.poll() is None:
                    ff.terminate()
                ff.wait()
                self.state.connected = False
            if not self.cfg.file_loop:
                self.state.finished = True
                return


def make_source(cfg: Config, state: SourceState):
    if cfg.source == "file":
        return FileSource(cfg, state)
    return KiwiSource(cfg, state)
