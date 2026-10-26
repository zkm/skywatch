import sys
import types
from dataclasses import replace

import numpy as np

from app.config import Config
from app.transcriber import WhisperTranscriber, looks_like_noise


class FakeModel:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return iter([]), None


def test_vad_off_by_default_and_opt_in(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    cfg = replace(Config(), data_dir=tmp_path)
    assert cfg.whisper_vad is False
    t = WhisperTranscriber(cfg)
    t.transcribe(np.zeros(16000, np.float32))
    assert t.model.calls[0]["vad_filter"] is False
    # no initial_prompt: Whisper recited it back on noise as fake Skykings
    assert "initial_prompt" not in t.model.calls[0]
    assert t.model.calls[0]["hallucination_silence_threshold"] == 2.0
    t = WhisperTranscriber(replace(cfg, whisper_vad=True))
    t.transcribe(np.zeros(16000, np.float32))
    assert t.model.calls[0]["vad_filter"] is True


# Transcripts from real recordings: three 5-10 minute noise blocks the squelch let
# through from the missileer.watch stream, and real HFGCS voice traffic.
NOISE = [
    "Shh Shh Shh Shh Shh Shh",
    "Mm hmm. Z-ray Yankee Zulu",
    "Thank you for watching!",
    "",
]
VOICE = [
    "Cupling Cupling this is Splendid Splendid my station authenticates Charlie Alpha Charlie with Juliet",
    "Last dollar, last dollar, this is me sale, me sale. I am excuse you Charlie, I'll meet you over.",
    "One Two Three Three Two One. This may go to town, out.",
    "Andrews, radio check, over.",
]


def test_looks_like_noise():
    assert all(looks_like_noise(t) for t in NOISE)
    assert not any(looks_like_noise(t) for t in VOICE)
