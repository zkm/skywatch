# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Skywatch monitors the USAF HFGCS shortwave network (11175 kHz USB) through public KiwiSDR receivers, transcribes each transmission with Whisper, and copies Emergency Action Messages character by character. FastAPI backend, plain-JS frontend with no build step, SQLite storage. See README.md for user-facing docs and the full environment-variable reference.

## Commands

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest   # ffmpeg must be on PATH

.venv/bin/python -m pytest -q                                   # all tests
.venv/bin/python -m pytest -q tests/test_phonetic.py::test_full_eam   # one test

# Run fully offline: synthetic audio, fake data, no Whisper, no KiwiSDR
.venv/bin/python tools/make_test_audio.py test.wav
DATA_DIR=./data PYTHONPATH=. .venv/bin/python tools/seed_demo.py      # fake EAM + Skyking + crowd votes
DATA_DIR=./data EAM_SOURCE=file EAM_FILE=test.wav EAM_FILE_LOOP=1 ASR_BACKEND=none \
  .venv/bin/uvicorn app.main:app --port 8000
# add PUSH_SUBJECT=mailto:ops@example.com to enable browser push locally

.venv/bin/python tools/build_pages.py _site                     # static demo, not published anymore (needs numpy + ffmpeg)
```

There is no linter or formatter configured.

## Architecture

**Audio pipeline (`app/pipeline.py`)** runs three daemon threads started from the FastAPI lifespan in `app/main.py`:
- `ingest`: reads 100 ms PCM chunks from the source (`source.py`: `KiwiSource` pipes `kiwirecorder.py` into ffmpeg; `FileSource` replays a file) and feeds both the live MP3 encoder (`live.py`) and the energy squelch (`segmenter.py`).
- `asr`: takes each completed `Transmission` off a queue, runs Whisper (`transcriber.py`, imported lazily), parses it (`phonetic.parse`), encodes the MP3, stores the row, emits the SSE `message` event and calls `pipeline.on_message` (wired to push notifications).
- `meter`: emits SSE `status` every 0.5 s.

Worker threads hand events to async clients through `live.Fanout`/`EventBus`, which uses `loop.call_soon_threadsafe` into per-client asyncio queues (slow clients drop data). SSE event names the UI relies on: `status`, `transmission`, `message`, `correction`, `aircraft`.

**Parsing (`app/phonetic.py`)** turns Whisper words into characters (NATO words, spoken digits, fuzzy misspellings, numerals). Low-probability words become `?`. It finds the header, body and "I say again" repeat from spoken markers, then merges the two reads position by position: a `?` in one read is filled from the other, and disagreements stay `?` with candidates kept in `chars[i].alts`. Its output shape (`body`, `copy1`, `copy2`, `chars[{c,t0,t1,p,alts}]`) is what the DB stores and the UI renders.

**Crowd corrections** (`corrections.py`, `store.correct`): votes are keyed `(message_id, pos, voter)` with upsert, so re-voting replaces. `voter` is a salted SHA-256 of the client IP; the salt lives in `DATA_DIR/voter.salt`. Consensus needs `CORRECTION_MIN_VOTES` with no tie. The result is denormalized into `messages.crowd_body` (NULL when unchanged) so list views don't recompute it. The machine `body` is never modified. `pos` is 0-based into `body`.

**Aircraft** (`aircraft.py`): `AircraftWatch` polls an ADSBExchange-v2-style feed (default adsb.lol `/v2/mil`), keeps watched ICAO types plus E-4B tails by registration, keeps trails in memory, and pushes snapshots over SSE. adsb.lol sends no CORS headers, so browsers can't query it directly; the server proxies it.

**Config** (`config.py`): every setting is an env var read into the `Config` dataclass. A new setting goes there, in `.env.example` and in the README's configuration table.

**Push** (`push.py`): `Notifier` sends each new message (filtered by `PUSH_KINDS`) to Web Push and, if `NTFY_URL` is set, to an ntfy topic. `Notifier.aircraft` is `AircraftWatch`'s `on_appear` callback and emails (`Mailer`, stdlib smtplib) when an E-4B is first heard; the `alerts` table (`store.claim_alert`) limits it to one email per aircraft per 6 h, across restarts. Web Push uses pywebpush, imported lazily. It stays disabled unless `PUSH_SUBJECT` is set to `mailto:…` or a path-less `https://host` (py_vapid rejects anything else). The VAPID key is generated at `DATA_DIR/vapid_private.pem`; replacing it breaks every existing subscription. Sends run on a thread pool with TTL 6 h; 404/410 responses delete the subscription. Subscribing is idempotent and sends the confirmation push only for a new endpoint, because the page re-syncs on every load.

**Store** (`store.py`): one SQLite connection behind a lock. The schema is `CREATE TABLE IF NOT EXISTS` plus ad-hoc `ALTER TABLE` migrations in `Store.__init__` (see `crowd_body`). Add new columns the same way so existing databases keep working.

**Frontend (`web/`)**: classic scripts sharing globals, loaded in order `app.js` → `trainer.js` → `aircraft.js` → `notify.js`. `app.js` defines the shared helpers (`$`, `api`, `getJSON`, `esc`, `state`) and a `hooks` object that later scripts chain onto (`hooks.view`, `hooks.aircraft`). Routing is hash-based (`#monitor`, `#trainer`, `#aircraft`) and runs on `DOMContentLoaded`, after every script has registered. All URLs are relative so the page works under `/skywatch/` on Pages. Colors are CSS custom properties with dark-mode overrides in two places (`prefers-color-scheme` and `[data-theme="dark"]`); keep them in sync. Leaflet loads lazily from cdnjs on the first visit to the aircraft tab. `sw.js` only handles push notifications and does no offline caching.

**Demo mode** (still supported, no longer published): `tools/build_pages.py` seeds a throwaway DB, dumps API responses as static JSON (`api/messages-<kind>.json`, `api/messages/<id>.json`, `api/aircraft.json`), and injects `window.SKYWATCH_DEMO = true` before `app.js` by string-replacing `<script src="app.js"></script>`, so keep that tag exactly as written in `index.html`. In demo mode the `api` helper switches to those files, and live audio, SSE, voting and push are disabled. New UI features need a `DEMO` branch.

**Production**: https://skywatch.zkm.tw runs on the `skywatch` DigitalOcean droplet (nyc3, 2 GB; Whisper small.en needs ~1.1 GB). `/opt/skywatch` is a clone of `main`; `/opt/skywatch-deploy` holds `compose.yml` (Skywatch plus Caddy for HTTPS, Skywatch's port unpublished), `Caddyfile` and the secret `skywatch.env`. Data lives in `/var/lib/skywatch`, with a nightly snapshot at 03:30 UTC in `/var/backups/skywatch`. Deploy with `git -C /opt/skywatch pull && docker compose -f /opt/skywatch-deploy/compose.yml up -d --build`. DNS is a DNS-only (unproxied) Cloudflare A record. Audio currently comes from the missileer.watch KPH stream (`EAM_SOURCE=file`), because the KiwiSDR receivers hang up after kiwirecorder authenticates.

## Constraints

- **CI installs only `numpy` and `pytest`** (no fastapi, faster-whisper or pywebpush). Tests must not import `app.main`, and modules they touch must keep heavy imports lazy (as `transcriber.py` and `push.py` do).
- **CI (`.github/workflows/ci.yml`)**: the `test` job runs on pushes and PRs and is a required status check on the protected `main` branch. It's the only job; the GitHub Pages demo was retired when the live site launched.
- **Dependabot** opens monthly PRs for GitHub Actions and pip. pip uses `versioning-strategy: increase-if-necessary` because `requirements.txt` declares minimums (`>=`) on purpose; don't raise them without a reason.
- **Static files**: UI assets are served with `Cache-Control: no-cache` (the `WebFiles` subclass in `main.py`). When checking UI changes in a browser, a stale cache is otherwise the usual cause of "my change didn't show up".
- **`TRUST_PROXY`**: behind a reverse proxy it must be `1`, or every listener shares one voter id and one rate-limit bucket.
