"""Runtime configuration, all from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _hosts(raw: str) -> list[tuple[str, int]]:
    out = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        host, _, port = item.partition(":")
        out.append((host, int(port or 8073)))
    return out


@dataclass
class Config:
    # --- source ---
    # "kiwi" (live KiwiSDR) or "file" (replay a local audio file, for testing)
    source: str = field(default_factory=lambda: _env("EAM_SOURCE", "kiwi"))
    kiwi_hosts: list[tuple[str, int]] = field(
        default_factory=lambda: _hosts(_env("KIWI_HOSTS", "")))
    kiwi_user: str = field(default_factory=lambda: _env("KIWI_USER", "skywatch"))
    kiwi_password: str = field(default_factory=lambda: _env("KIWI_PASSWORD", ""))
    kiwirecorder: str = field(
        default_factory=lambda: _env("KIWIRECORDER", "/opt/kiwiclient/kiwirecorder.py"))
    freq_khz: float = field(default_factory=lambda: float(_env("FREQ_KHZ", "11175")))
    mode: str = field(default_factory=lambda: _env("MODE", "usb"))
    passband_lo: int = field(default_factory=lambda: int(_env("PASSBAND_LO", "300")))
    passband_hi: int = field(default_factory=lambda: int(_env("PASSBAND_HI", "2700")))
    file_path: str = field(default_factory=lambda: _env("EAM_FILE", ""))
    file_loop: bool = field(default_factory=lambda: _env("EAM_FILE_LOOP", "0") == "1")
    reconnect_backoff_s: float = field(
        default_factory=lambda: float(_env("RECONNECT_BACKOFF", "15")))

    # --- audio ---
    sample_rate: int = 16000  # what the pipeline (and Whisper) runs at

    # --- segmentation (energy squelch) ---
    open_db: float = field(default_factory=lambda: float(_env("SQUELCH_OPEN_DB", "8")))
    close_db: float = field(default_factory=lambda: float(_env("SQUELCH_CLOSE_DB", "5")))
    hang_s: float = field(default_factory=lambda: float(_env("SQUELCH_HANG_S", "3.0")))
    preroll_s: float = field(default_factory=lambda: float(_env("PREROLL_S", "1.0")))
    min_tx_s: float = field(default_factory=lambda: float(_env("MIN_TX_S", "4.0")))
    max_tx_s: float = field(default_factory=lambda: float(_env("MAX_TX_S", "600")))

    # --- transcription ---
    asr_backend: str = field(default_factory=lambda: _env("ASR_BACKEND", "whisper"))
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small.en"))
    whisper_device: str = field(default_factory=lambda: _env("WHISPER_DEVICE", "cpu"))
    whisper_compute: str = field(default_factory=lambda: _env("WHISPER_COMPUTE", "int8"))
    min_word_prob: float = field(default_factory=lambda: float(_env("MIN_WORD_PROB", "0.45")))
    keep_non_eam: bool = field(default_factory=lambda: _env("KEEP_NON_EAM", "1") == "1")

    # --- storage / web ---
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    live_bitrate: str = field(default_factory=lambda: _env("LIVE_BITRATE", "32k"))

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "skywatch.sqlite3"


config = Config()
