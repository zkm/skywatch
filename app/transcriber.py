"""ASR backends. Each returns (full_text, [Word]) for a 16 kHz float32 mono array."""
from __future__ import annotations

import logging
import re
from typing import Protocol

import numpy as np

from .config import Config
from .phonetic import Word

log = logging.getLogger("skywatch.asr")

# Biases Whisper toward the HFGCS vocabulary instead of "Charlie" -> "Charley" etc.
# (No initial_prompt: on long stretches of noise Whisper recites the prompt back,
# which produced fake "Skyking, Skyking, do not answer" messages.)
HOTWORDS = ("Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima "
            "Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey "
            "X-ray Yankee Zulu Niner Skyking")


FILLER = {"mm", "hmm", "mhm", "shh", "uh", "um", "ah", "oh", "the", "a"}
# stock phrases Whisper produces from noise (learned from video captions)
STOCK = ("thank you for watching", "thanks for watching", "subscribe", "see you next time",
         "please like", "bon appetit")


def looks_like_noise(text: str) -> bool:
    """True for transcripts that are Whisper hallucinating on noise rather than voice traffic."""
    t = text.lower()
    if any(p in t for p in STOCK):
        return True
    words = {w for w in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", t) if w not in FILLER}
    return len(words) <= 3      # "Shh Shh Shh", "Mm hmm. Z-ray Yankee Zulu"


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> tuple[str, list[Word]]: ...


class WhisperTranscriber:
    def __init__(self, cfg: Config) -> None:
        from faster_whisper import WhisperModel  # heavy import; only when used
        log.info("loading whisper model %s on %s/%s",
                 cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute)
        self.model = WhisperModel(cfg.whisper_model, device=cfg.whisper_device,
                                  compute_type=cfg.whisper_compute,
                                  download_root=str(cfg.data_dir / "models"))
        self.vad = cfg.whisper_vad

    def transcribe(self, audio: np.ndarray) -> tuple[str, list[Word]]:
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=5,
            word_timestamps=True,
            hotwords=HOTWORDS,
            hallucination_silence_threshold=2.0,   # skip text Whisper invents over silent stretches
            condition_on_previous_text=False,   # avoids runaway repetition on long EAMs
            vad_filter=self.vad,
            vad_parameters={"min_silence_duration_ms": 1500} if self.vad else None,
        )
        words: list[Word] = []
        text_parts: list[str] = []
        for seg in segments:
            text_parts.append(seg.text.strip())
            for w in seg.words or []:
                words.append(Word(w.word.strip(), float(w.start), float(w.end),
                                  float(w.probability)))
        return " ".join(text_parts).strip(), words


class NullTranscriber:
    """No ASR: everything is archived as untranscribed voice. Useful for testing."""

    def transcribe(self, audio: np.ndarray) -> tuple[str, list[Word]]:
        return "", []


def make_transcriber(cfg: Config) -> Transcriber:
    if cfg.asr_backend == "none":
        return NullTranscriber()
    return WhisperTranscriber(cfg)
