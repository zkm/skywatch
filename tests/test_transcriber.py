import sys
import types
from dataclasses import replace

import numpy as np

from app.config import Config
from app.transcriber import WhisperTranscriber


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
    t = WhisperTranscriber(replace(cfg, whisper_vad=True))
    t.transcribe(np.zeros(16000, np.float32))
    assert t.model.calls[0]["vad_filter"] is True
