import numpy as np

from app.segmenter import Segmenter

SR = 16000


def noise(sec, amp, rng):
    return (rng.standard_normal(int(sec * SR)) * amp).astype(np.int16)


def voice(sec, amp, rng):
    t = np.arange(int(sec * SR)) / SR
    # syllable-rate amplitude modulation with short gaps, like spoken phonetics
    env = (np.sin(2 * np.pi * 2.0 * t) > -0.3).astype(float)
    tone = np.sin(2 * np.pi * 800 * t) + 0.5 * np.sin(2 * np.pi * 1400 * t)
    return (tone * env * amp + rng.standard_normal(len(t)) * 300).astype(np.int16)


def run(audio, max_tx_s=600):
    got = []
    clock = [0.0]
    seg = Segmenter(SR, 8, 5, 3.0, 1.0, 4.0, max_tx_s, got.append, clock=lambda: clock[0])
    for i in range(0, len(audio), 1600):
        clock[0] = (i + 1600) / SR
        seg.feed(audio[i:i + 1600].tobytes())
    seg.flush()
    return got


def test_single_transmission_with_pauses():
    rng = np.random.default_rng(1)
    gap = noise(1.5, 300, rng)             # pause shorter than hang: stays one tx
    audio = np.concatenate([noise(10, 300, rng), voice(8, 6000, rng), gap,
                            voice(8, 6000, rng), noise(10, 300, rng)])
    txs = run(audio)
    assert len(txs) == 1
    tx = txs[0]
    assert 17 < tx.duration < 22, tx.duration
    assert 8.5 < tx.started_at < 10.5, tx.started_at


def test_two_transmissions_and_short_blip_ignored():
    rng = np.random.default_rng(2)
    audio = np.concatenate([noise(5, 300, rng), voice(6, 6000, rng), noise(8, 300, rng),
                            voice(1, 6000, rng), noise(8, 300, rng),   # 1 s blip < MIN_TX
                            voice(6, 6000, rng), noise(6, 300, rng)])
    assert len(run(audio)) == 2


def test_noise_only():
    rng = np.random.default_rng(3)
    assert run(noise(60, 300, rng)) == []


def test_stale_floor_is_remeasured_after_a_max_length_transmission():
    # background noise jumps 20 dB and stays there (gain change, band opening)
    rng = np.random.default_rng(3)
    audio = np.concatenate([noise(10, 300, rng), noise(200, 3000, rng),
                            voice(8, 30000, rng), noise(20, 3000, rng)])
    txs = run(audio, max_tx_s=60)
    # one max-length capture of the new noise, then the floor catches up: no endless
    # back-to-back 60 s captures, and the later real transmission still opens it
    assert sum(t.duration >= 59 for t in txs) == 1, [round(t.duration) for t in txs]
    assert any(5 < t.duration < 15 for t in txs), [round(t.duration) for t in txs]
