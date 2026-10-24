"""Generate a test WAV: HF-like noise with two voice-like bursts.

    python tools/make_test_audio.py test.wav
    EAM_SOURCE=file EAM_FILE=test.wav ASR_BACKEND=none uvicorn app.main:app
"""
import sys
import wave

import numpy as np

SR = 16000
rng = np.random.default_rng(7)


def noise(sec):
    return rng.standard_normal(int(sec * SR)) * 400


def burst(sec):
    t = np.arange(int(sec * SR)) / SR
    env = (np.sin(2 * np.pi * 2.2 * t) > -0.3).astype(float)
    tone = np.sin(2 * np.pi * 700 * t) + 0.6 * np.sin(2 * np.pi * 1300 * t)
    return tone * env * 7000 + noise(sec)


audio = np.concatenate([noise(6), burst(12), noise(8), burst(7), noise(8)])
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(np.clip(audio, -32768, 32767).astype(np.int16).tobytes())
print(f"wrote {sys.argv[1]} ({len(audio) / SR:.0f}s)")
