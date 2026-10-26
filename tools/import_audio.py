"""Import a recording into the archive, as if Skywatch had copied it live.

    DATA_DIR=./data PYTHONPATH=. python tools/import_audio.py clip.opus \\
        --at 2026-09-24T16:36:10Z --source "missileer.watch (Iceland receiver)" --tries 3

The recording goes through the same steps as live traffic (two-pass Whisper, the
parser, the noise filter) and is stored with its audio, so it shows up in the log,
the copy pad and the trainer. --source is shown on the page as the recording's
credit. --tries re-runs Whisper and keeps the best EAM copy, since the careful pass
varies from run to run. With --eam-only, anything that doesn't parse as an EAM is
skipped instead of stored.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import Config, config
from app.phonetic import parse
from app.pipeline import encode_mp3
from app.store import Store
from app.transcriber import copy_score, looks_like_noise

SR = 16000


@dataclass
class Imported:
    id: Optional[int]          # None when skipped
    kind: str
    body: Optional[str]
    reason: str = ""


def load_audio(path: Path) -> np.ndarray:
    """Any format ffmpeg reads, as 16 kHz mono int16 (the pipeline's format)."""
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
                          "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.int16)


def import_recording(samples: np.ndarray, *, started_at: float, source: str, store: Store,
                     transcriber, cfg: Config = config, tries: int = 1,
                     eam_only: bool = False) -> Imported:
    audio = samples.astype(np.float32) / 32768.0
    best = None
    for _ in range(max(1, tries)):
        text, words = transcriber.transcribe(audio)
        parsed = parse(words, cfg.min_word_prob)
        if best is None or (parsed.kind == best[2].kind and copy_score(parsed) > copy_score(best[2])) \
                or (best[2].kind == "voice" and parsed.kind != "voice"):
            best = (text, words, parsed)
    text, words, parsed = best

    if parsed.kind == "voice" and looks_like_noise(text):
        return Imported(None, parsed.kind, None, "transcript looks like noise")
    if eam_only and parsed.kind != "eam":
        return Imported(None, parsed.kind, None, f"parsed as {parsed.kind}, not an EAM")

    stamp = datetime.fromtimestamp(started_at, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"{stamp}_{parsed.kind}_import.mp3"
    cfg.audio_dir.mkdir(parents=True, exist_ok=True)
    encode_mp3(samples, SR, cfg.audio_dir / fname)
    msg_id = store.insert({
        "started_at": started_at,
        "duration": round(len(samples) / SR, 2),
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
        "source": f"import:{source}",    # the UI shows this as the recording's credit
    })
    return Imported(msg_id, parsed.kind, parsed.body)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--at", required=True, action="append",
                    help="UTC start time per file, e.g. 2026-09-24T16:36:10Z (repeat for several files)")
    ap.add_argument("--source", required=True, help="credit shown with the recording")
    ap.add_argument("--tries", type=int, default=1)
    ap.add_argument("--eam-only", action="store_true")
    args = ap.parse_args(argv)
    if len(args.at) != len(args.files):
        ap.error("give one --at per file")

    from app.transcriber import WhisperTranscriber   # heavy; only for real runs
    store, asr = Store(config.db_path), WhisperTranscriber(config)
    for path, at in zip(args.files, args.at):
        started = datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        r = import_recording(load_audio(path), started_at=started, source=args.source, store=store,
                             transcriber=asr, tries=args.tries, eam_only=args.eam_only)
        print(f"{path.name}: " + (f"stored #{r.id} {r.kind} {r.body or ''}" if r.id else f"skipped ({r.reason})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
