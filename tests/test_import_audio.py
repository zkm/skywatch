import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from app.config import Config
from app.store import Store
from test_phonetic import BODY, eam_text, words

spec = importlib.util.spec_from_file_location(
    "import_audio", Path(__file__).resolve().parent.parent / "tools" / "import_audio.py")
import_audio = importlib.util.module_from_spec(spec)
sys.modules["import_audio"] = import_audio      # @dataclass looks its module up here
spec.loader.exec_module(import_audio)


class Scripted:
    """Fake transcriber: returns the given word lists in turn."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)

    def transcribe(self, audio):
        ws = self.outputs.pop(0)
        return " ".join(w.text for w in ws), ws


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(import_audio, "encode_mp3", lambda samples, sr, path: Path(path).write_bytes(b"mp3"))
    cfg = replace(Config(), data_dir=tmp_path)
    return cfg, Store(cfg.db_path)


def run(cfg, store, transcriber, **kw):
    return import_audio.import_recording(np.zeros(16000 * 5, np.int16), started_at=1790000000.0,
                                         source="missileer.watch (Iceland receiver)", store=store,
                                         transcriber=transcriber, cfg=cfg, **kw)


def test_eam_is_stored_like_live_traffic_with_its_credit(tmp_path, monkeypatch):
    cfg, store = setup(tmp_path, monkeypatch)
    r = run(cfg, store, Scripted(words(eam_text(BODY, BODY))))
    m = store.get(r.id)
    assert m["kind"] == "eam" and m["body"] == BODY and m["duration"] == 5.0
    assert m["source"] == "import:missileer.watch (Iceland receiver)"
    assert (cfg.audio_dir / m["audio"]).exists() and m["audio"].endswith("_eam_import.mp3")
    assert store.list(["eam"])[0]["id"] == r.id          # visible in the log and trainer


def test_tries_keep_the_best_copy(tmp_path, monkeypatch):
    cfg, store = setup(tmp_path, monkeypatch)
    rough = words(eam_text(BODY, BODY), low=set(range(10, 40)))
    clean = words(eam_text(BODY, BODY))
    r = run(cfg, store, Scripted(rough, clean, rough), tries=3)
    assert store.get(r.id)["body"] == BODY


def test_eam_only_skips_other_traffic_and_noise_is_never_stored(tmp_path, monkeypatch):
    cfg, store = setup(tmp_path, monkeypatch)
    r = run(cfg, store, Scripted(words("Andrews radio check over and out")), eam_only=True)
    assert r.id is None and "not an EAM" in r.reason
    r = run(cfg, store, Scripted(words("Thank you very much. Thank you.")))
    assert r.id is None and "noise" in r.reason
    assert store.list(["eam", "voice", "skyking"]) == []
