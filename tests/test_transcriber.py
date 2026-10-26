import sys
import types
from dataclasses import replace

import numpy as np

from app.config import Config
from app.transcriber import WhisperTranscriber, copy_score, looks_like_noise
from test_phonetic import BODY, eam_text, words


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
    "Thank you very much. Thank you. Thank you very much.",
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


class Seg:
    """Minimal faster-whisper segment built from parser test words."""

    def __init__(self, ws):
        self.text = " ".join(w.text for w in ws)
        self.words = [types.SimpleNamespace(word=w.text, start=w.start, end=w.end, probability=w.prob)
                      for w in ws]


def two_pass_model(fast, careful):
    """A fake WhisperModel whose output depends on whether the call is the careful pass."""
    class Model(FakeModel):
        def transcribe(self, audio, **kw):
            self.calls.append(kw)
            return iter([Seg(careful if "hotwords" in kw else fast)]), None
    return Model


def run_two_pass(monkeypatch, tmp_path, fast, careful):
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=two_pass_model(fast, careful)))
    t = WhisperTranscriber(replace(Config(), data_dir=tmp_path))
    text, _words = t.transcribe(np.zeros(16000, np.float32))
    return t.model.calls, text


def test_voice_and_noise_get_only_the_fast_pass(monkeypatch, tmp_path):
    calls, text = run_two_pass(monkeypatch, tmp_path, words("Andrews radio check over"), [])
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.0 and "hotwords" not in calls[0]   # no fallback, no leaks
    assert text == "Andrews radio check over"


def test_eam_gets_the_careful_pass(monkeypatch, tmp_path):
    eam = words(eam_text(BODY, BODY))
    calls, _text = run_two_pass(monkeypatch, tmp_path, eam, eam)
    assert len(calls) == 2 and "hotwords" in calls[1] and "temperature" not in calls[1]


def test_careful_copy_kept_only_when_it_is_better(monkeypatch, tmp_path):
    rough = words(eam_text(BODY, BODY), low=set(range(10, 40)))   # many unsure words -> ?s
    clean = words(eam_text(BODY, BODY))
    calls, text = run_two_pass(monkeypatch, tmp_path, rough, clean)
    assert text == " ".join(w.text for w in clean)                 # careful copy is better: kept
    calls, text = run_two_pass(monkeypatch, tmp_path, clean, rough)
    assert text == " ".join(w.text for w in clean)                 # careful copy is worse: fast kept


def test_copy_score_penalizes_repetition_loops():
    from app.phonetic import Parsed
    loop = Parsed(kind="eam", body="WE662U???V?VON?3C???CCCCCCCC")
    plain = Parsed(kind="eam", body="WE662UR??V76?ONY3CWGC3CZ")
    assert copy_score(plain) > copy_score(loop)


def test_careful_pass_kept_only_if_it_still_parses_as_eam(monkeypatch, tmp_path):
    fast = words(eam_text(BODY, BODY))
    calls, text = run_two_pass(monkeypatch, tmp_path, fast, words("X-ray Yankee Zulu"))
    assert len(calls) == 2
    assert text == " ".join(w.text for w in fast)   # careful pass hallucinated: keep the fast copy
