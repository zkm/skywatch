"""ASR backends. Each returns (full_text, [Word]) for a 16 kHz float32 mono array."""
from __future__ import annotations

import logging
import re
from typing import Protocol

import numpy as np

from .config import Config
from .phonetic import Word, parse

log = logging.getLogger("skywatch.asr")

# Biases Whisper toward the HFGCS vocabulary instead of "Charlie" -> "Charley" etc.
# (No initial_prompt: on long stretches of noise Whisper recites the prompt back,
# which produced fake "Skyking, Skyking, do not answer" messages.)
HOTWORDS = ("Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima "
            "Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey "
            "X-ray Yankee Zulu Niner Skyking")


FILLER = {"mm", "hmm", "mhm", "shh", "uh", "um", "ah", "oh", "the", "a",
          "thank", "thanks", "you", "very", "much"}   # "Thank you very much. Thank you." from noise
# stock phrases Whisper produces from noise (learned from video captions)
STOCK = ("thank you for watching", "thanks for watching", "subscribe", "see you next time",
         "please like", "bon appetit")


def copy_score(p) -> int:
    """How much of a parsed copy is real: known characters, minus Whisper's repetition loops.

    Runs of 3+ identical characters ("ZZZZZZZZ", "CCCCCCCC") are decoding loops, not an EAM;
    every character past the second in such a run counts against the copy.
    """
    if p.kind == "skyking":
        return sum(bool(v) and "?" not in str(v) for v in (p.skyking or {}).values())
    body = p.body or ""
    known = sum(c != "?" for c in body)
    looped = sum(1 for i in range(2, len(body)) if body[i] == body[i - 1] == body[i - 2] != "?")
    return known - 2 * looped


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
        self.min_word_prob = cfg.min_word_prob

    def transcribe(self, audio: np.ndarray) -> tuple[str, list[Word]]:
        """Two passes. A fast, literal pass for everything; a careful pass only for EAMs/Skykings.

        The fast pass (no hotwords, no temperature fallback) is ~3x quicker and on noise
        produces nothing or plain filler. The careful pass (hotwords + fallback) copies EAMs
        far better, but on noise it recites the hotwords back and can loop for minutes, so
        it only runs when the fast pass already found EAM or Skyking structure.
        """
        text, words = self._run(audio, careful=False)
        fast = parse(words, self.min_word_prob)
        if fast.kind not in ("eam", "skyking"):
            return text, words
        text2, words2 = self._run(audio, careful=True)
        careful = parse(words2, self.min_word_prob)
        # The careful pass samples on fallback, so its copy varies run to run; keep whichever
        # copy is better rather than trusting it blindly.
        if careful.kind == fast.kind and copy_score(careful) > copy_score(fast):
            return text2, words2
        return text, words

    def _run(self, audio: np.ndarray, careful: bool) -> tuple[str, list[Word]]:
        opts = {"hotwords": HOTWORDS} if careful else {"temperature": 0.0}
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=5,
            word_timestamps=True,
            hallucination_silence_threshold=2.0,   # skip text Whisper invents over silent stretches
            condition_on_previous_text=False,   # avoids runaway repetition on long EAMs
            vad_filter=self.vad,
            vad_parameters={"min_silence_duration_ms": 1500} if self.vad else None,
            **opts,
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
