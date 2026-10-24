# skywatch

Monitors HFGCS on 11175 kHz USB through public KiwiSDR receivers, cuts out each
transmission, transcribes it with Whisper, and copies Emergency Action Messages
character by character. Serves a live MP3 stream, an archive, and a copy-pad UI.

```
KiwiSDR ──kiwirecorder --nc-wav──▶ ffmpeg (16 kHz) ──┬─▶ live MP3 encoder ─▶ /live.mp3
                                                     └─▶ energy squelch ─▶ Whisper ─▶ EAM parser ─▶ SQLite + MP3
                                                                                              └─▶ /api/events (SSE)
```

## Run

```sh
cp .env.example .env          # set KIWI_HOSTS=host:port[,host:port...]
docker compose up --build     # http://localhost:8000
```

Without Docker:

```sh
git clone --depth 1 https://github.com/jks-prv/kiwiclient.git /opt/kiwiclient
pip install -r requirements.txt              # ffmpeg must be on PATH
export $(grep -v '^#' .env | xargs) KIWIRECORDER=/opt/kiwiclient/kiwirecorder.py
uvicorn app.main:app --port 8000
```

First start downloads the Whisper model into `DATA_DIR/models` (needs huggingface.co).

## Offline testing

```sh
python tools/make_test_audio.py test.wav                       # noise + two voice-like bursts
DATA_DIR=./data PYTHONPATH=. python tools/seed_demo.py          # one fake EAM + Skyking (callsign DEMO)
EAM_SOURCE=file EAM_FILE=test.wav ASR_BACKEND=none uvicorn app.main:app
python -m pytest -q
```

`EAM_SOURCE=file` also takes a real recording (any format ffmpeg reads), which
is the easiest way to tune the squelch and judge Whisper model size.

## How copying works

On-air format (per priyom.org): preamble, first six characters + "stand by" x3,
"Message follows" (or "Message of N characters follows"), ~30 characters,
"I say again", the characters again, "This is <callsign>, out".

`app/phonetic.py`:

- Maps NATO words and spoken digits (incl. "niner", fuzzy misspellings, and
  numerals Whisper emits like "25") to characters.
- Words under `MIN_WORD_PROB`, or unknown words wedged between characters, become `?`.
- Locates header, body, and repeat using the spoken markers; merges the two
  sends position by position. A `?` in one send is filled from the other; a
  disagreement stays `?` with both candidates kept in `alts`.
- Falls back to the longest phonetic run (>= 20 chars) when markers are missed.
- Skykings: codeword, minutes, and authentication pair.

## API

| Route | |
|---|---|
| `GET /live.mp3` | live 32 kbps stream (silence-filled when the receiver is down) |
| `GET /api/messages?kind=eam\|skyking\|voice\|all&limit=&before=` | archive, newest first |
| `GET /api/messages/{id}` | full record: per-char timestamps, both sends, ASR words |
| `GET /api/status` | receiver, level/floor dB, squelch, ASR queue, listeners |
| `GET /api/events` | SSE: `status` every 0.5 s, `transmission`, `message` |
| `GET /audio/<file>.mp3` | archived recording |

## Tuning

- `SQUELCH_OPEN_DB` / `SQUELCH_CLOSE_DB` are dB over the tracked noise floor. The
  UI meter shows the threshold mark; raise it if QRM opens the squelch.
- `SQUELCH_HANG_S` must exceed the gaps between character groups or an EAM splits.
- `WHISPER_MODEL=small.en` runs near real time on a few CPU cores; `medium.en`
  or `WHISPER_DEVICE=cuda` copies noticeably better on weak signals.
- Public Kiwis drop users (time limits, full slots). List several in `KIWI_HOSTS`.

## Not built yet

Crowd corrections, aircraft map / ADS-B alerts, push/email notifications, and
the copy trainer. The schema keeps per-character timestamps and alternates so
a corrections table can reference positions directly.
