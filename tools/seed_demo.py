"""Insert one synthetic EAM and one Skyking so the UI can be developed offline.

    DATA_DIR=./data python tools/seed_demo.py

The rows are clearly fake (callsign DEMO) and the audio is a tone.
"""
import time

import numpy as np

from app.config import config
from app.phonetic import NATO, Word, parse
from app.pipeline import encode_mp3
from app.store import Store

SPELL = {v: k.capitalize() for k, v in NATO.items()
         if k not in ("alfa", "juliett", "whisky", "tree", "fower", "fife", "niner")}
SPELL["X"] = "X-ray"
SPELL["9"] = "Niner"


def words(text, low=()):
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append(Word(w, t, t + 0.45, 0.2 if i in low else 0.93))
        t += 0.6
    return out


def spell(s):
    return " ".join(SPELL[c] for c in s)


body = "QX7RTZ2KLMA9BWC4DEF3GH8JNS5UVP"
text = (f"All stations, all stations, this is Demo, Demo. {spell(body[:6])}, stand by. "
        f"Message follows. {spell(body)} I say again {spell(body)} This is Demo, out.")
ws = words(text)
mf = next(i for i, w in enumerate(ws) if w.text.startswith("follows")) + 1
for off in (4, 11):          # unsure in first copy, recovered from second
    ws[mf + off].prob = 0.2
sa = next(i for i, w in enumerate(ws) if w.text == "again") + 1
for off in (11, 20):         # unsure in both copies at position 21
    ws[sa + off].prob = 0.2
ws[mf + 20].prob = 0.2

store = Store(config.db_path)
config.audio_dir.mkdir(parents=True, exist_ok=True)


def save(kind, wlist, transcript, dur_s):
    p = parse(wlist)
    t = np.arange(int(dur_s * 16000)) / 16000
    tone = (np.sin(2 * np.pi * 800 * t) * 3000 * (np.sin(2 * np.pi * 1.6 * t) > 0)).astype(np.int16)
    fname = f"demo_{kind}_{int(time.time())}.mp3"
    encode_mp3(tone, 16000, config.audio_dir / fname)
    store.insert({
        "started_at": time.time() - 600, "duration": dur_s, "kind": p.kind,
        "callsign": p.callsign, "header": p.header or None, "body": p.body or None,
        "copy1": p.copy1 or None, "copy2": p.copy2 or None, "expected_len": p.expected_len,
        "unknowns": p.body.count("?") if p.body else None,
        "chars": [c.__dict__ for c in p.chars] or None, "skyking": p.skyking,
        "transcript": transcript, "words": [w.__dict__ for w in wlist],
        "audio": fname, "source": "demo", "freq_khz": 11175, "peak_db": -20, "floor_db": -40,
    })
    print(kind, p.body or p.skyking)


sky = ("Skyking, Skyking, do not answer. Iron Maiden, Iron Maiden. Time two five. "
       "Authentication Papa Sierra. This is Demo, out.")
save("skyking", words(sky), sky, 20.0)
save("eam", ws, text, ws[-1].end + 1)
