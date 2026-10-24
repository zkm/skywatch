"""ASR backends. Each returns (full_text, [Word]) for a 16 kHz float32 mono array."""
from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

from .config import Config
from .phonetic import Word

log = logging.getLogger("skywatch.asr")

# Biases Whisper toward the HFGCS vocabulary instead of "Charlie" -> "Charley" etc.
PROMPT = (
    "All stations, all stations, this is Andrews, Andrews. "
    "Alpha Bravo Charlie Delta Echo Foxtrot, stand by. "
    "Message follows. Golf Hotel India Juliet Kilo Lima Mike November Oscar Papa "
    "Quebec Romeo Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu "
    "Zero One Two Three Four Five Six Seven Eight Niner. I say again. "
    "Skyking, Skyking, do not answer. Time two five. Authentication Papa Sierra. "
    "This is Offutt, out."
)
HOTWORDS = ("Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima "
            "Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey "
            "X-ray Yankee Zulu Niner Skyking")


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

    def transcribe(self, audio: np.ndarray) -> tuple[str, list[Word]]:
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=5,
            word_timestamps=True,
            initial_prompt=PROMPT,
            hotwords=HOTWORDS,
            condition_on_previous_text=False,   # avoids runaway repetition on long EAMs
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 1500},
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
