# Skywatch

Monitors HFGCS on 11175 kHz USB through public KiwiSDR receivers, cuts out each
transmission, transcribes it with Whisper, and copies Emergency Action Messages
character by character. Serves a live MP3 stream, an archive, and a copy-pad UI
where listeners correct the machine copy, plus a copy trainer and a map of
strategic aircraft from public ADS-B.

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

## Demo site

Every push to `main` builds a static demo with the sample EAM and Skyking (and an
ADS-B snapshot) and deploys it to GitHub Pages (`.github/workflows/pages.yml`):
https://zkm.github.io/skywatch/. No live audio there; it only shows the UI.
Build it locally with `python tools/build_pages.py _site`.

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
| `GET /api/messages/{id}` | full record: per-char timestamps, both sends, ASR words, `corrections` tallies |
| `POST /api/messages/{id}/corrections` | `{"pos": 0-based, "char": "A"}`: one vote; re-voting replaces yours |
| `GET /api/aircraft` | watched aircraft with recent trails |
| `GET /api/push/key` | VAPID public key (503 when push is off) |
| `POST /api/push/subscribe` / `unsubscribe` | browser `PushSubscription.toJSON()` / `{"endpoint"}` |
| `GET /api/status` | receiver, level/floor dB, squelch, ASR queue, listeners |
| `GET /api/events` | SSE: `status` every 0.5 s, `message`, `correction`, `aircraft` |
| `GET /audio/<file>.mp3` | archived recording |

## Tuning

- `SQUELCH_OPEN_DB` / `SQUELCH_CLOSE_DB` are dB over the tracked noise floor. The
  UI meter shows the threshold mark; raise it if QRM opens the squelch.
- `SQUELCH_HANG_S` must exceed the gaps between character groups or an EAM splits.
- `WHISPER_MODEL=small.en` runs near real time on a few CPU cores; `medium.en`
  or `WHISPER_DEVICE=cuda` copies noticeably better on weak signals.
- Public Kiwis drop users (time limits, full slots). List several in `KIWI_HOSTS`.

## Crowd corrections

Select any character in an EAM copy to hear it and vote on what it really was
(arrow keys move, typing a letter starts a vote). A position switches to the
crowd's character once `CORRECTION_MIN_VOTES` listeners agree and no other
character ties it; the machine copy is always kept. Voters are identified by a
salted hash of their IP (`DATA_DIR/voter.salt`), one vote per position, and
rate-limited by `CORRECTION_RATE` per hour. Behind a reverse proxy set
`TRUST_PROXY=1` or every listener looks like the proxy.

## Copy trainer

Plays a random archived EAM and lets you copy it into a grid, then marks each
character against the reference (machine copy plus crowd corrections). Any
positions nobody has copied yet can be sent straight in as corrections. It runs
in the browser, so it works on the demo site too.

## Strategic aircraft

Polls `ADSB_URL` (adsb.lol's military feed by default) every `ADSB_POLL_S` and
keeps E-6B, E-4B, B-52H, B-2 and WC-135/OC-135 by ICAO type, plus the four E-4B
tails by registration (`app/aircraft.py`). Positions keep a ~1 hour trail and
drop off 15 minutes after an aircraft was last heard. These aircraft often fly
without ADS-B, so an empty map is the usual state. The demo site shows a
snapshot taken when it was built.

## Notifications

"Notify me" in the header subscribes the browser to Web Push, and every new EAM
then arrives as a system notification, even with the tab closed. Turn it on by
setting `PUSH_SUBJECT` (`mailto:you@example.com`, or `https://your.host` with
no path): push services use it to contact whoever runs the server, and the
button stays hidden until it's set. The VAPID key is generated on first start at
`DATA_DIR/vapid_private.pem`; keep it, since replacing it silently breaks every
existing subscription. `PUSH_KINDS` picks what notifies (`eam`, `skyking`,
`voice`).

- iPhone/iPad only allow web push for sites added to the Home Screen; the button
  explains this when tapped in Safari.
- Push needs HTTPS in production (localhost is exempt).
- `NTFY_URL=https://ntfy.sh/<topic>` also posts each message to an ntfy topic,
  for people who'd rather use the ntfy app.

## Not built yet

An email alert when the E-4B appears.
