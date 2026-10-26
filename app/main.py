"""FastAPI app: live stream, archive API, SSE, static UI."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .aircraft import AircraftWatch
from .config import config
from .corrections import VALID_CHAR, RateLimiter, load_salt, voter_id
from .live import EventBus, LiveEncoder
from .pipeline import Pipeline
from .push import Mailer, Notifier, valid_subscription
from .store import Store

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
KINDS = {"eam", "skyking", "voice"}

store = Store(config.db_path)
events = EventBus(maxsize=256)
live: Optional[LiveEncoder] = None
pipeline: Optional[Pipeline] = None
aircraft: Optional[AircraftWatch] = None
salt = load_salt(config.salt_path)
limiter = RateLimiter(config.correction_rate, 3600)
mailer = Mailer(config.smtp_host, config.smtp_port, config.smtp_user, config.smtp_password,
                config.smtp_from, config.alert_email_to)
notifier = Notifier(store, config.push_kinds, config.vapid_path, config.push_subject, config.ntfy_url,
                    mailer)
push_limiter = RateLimiter(20, 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global live, pipeline, aircraft
    loop = asyncio.get_running_loop()
    live = LiveEncoder(config.sample_rate, config.live_bitrate)
    live.fanout.bind(loop)
    events.bind(loop)
    pipeline = Pipeline(config, store, events, live)
    pipeline.on_message = notifier.message
    pipeline.start()
    if config.adsb_enabled:
        aircraft = AircraftWatch(config.adsb_url, config.adsb_poll_s,
                                 lambda snap: events.emit("aircraft", snap), notifier.aircraft)
        aircraft.start()
    yield
    if aircraft:
        aircraft.stop()
    pipeline.stop()
    live.close()


app = FastAPI(title="Skywatch", lifespan=lifespan)
config.audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=config.audio_dir), name="audio")


@app.get("/api/status")
def status():
    assert pipeline is not None
    return {**pipeline.status(), "counts": store.counts(),
            "min_votes": config.correction_min_votes}


@app.get("/api/messages")
def messages(kind: str = Query("eam", description="comma list of eam,skyking,voice or 'all'"),
             limit: int = 50, before: Optional[int] = None):
    kinds = None if kind == "all" else [k for k in kind.split(",") if k in KINDS]
    return store.list(kinds, limit, before)


@app.get("/api/messages/{msg_id}")
def message(msg_id: int):
    m = store.get(msg_id)
    if not m:
        raise HTTPException(404, "no such message")
    return m


class Correction(BaseModel):
    pos: int    # 0-based position in the message body
    char: str


def client_ip(request: Request) -> str:
    if config.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/messages/{msg_id}/corrections")
def correct(msg_id: int, c: Correction, request: Request):
    m = store.get(msg_id)
    if not m:
        raise HTTPException(404, "no such message")
    if m["kind"] != "eam" or not m["body"]:
        raise HTTPException(400, "only EAM copies take corrections")
    char = c.char.strip().upper()
    if not VALID_CHAR.match(char):
        raise HTTPException(400, "one letter or digit")
    if not 0 <= c.pos < len(m["body"]):
        raise HTTPException(400, "position out of range")
    voter = voter_id(salt, client_ip(request))
    if not limiter.allow(voter):
        raise HTTPException(429, "too many corrections; try again later")
    result = store.correct(msg_id, c.pos, char, voter, config.correction_min_votes, time.time())
    events.emit("correction", result)
    return {**result, "min_votes": config.correction_min_votes}


@app.get("/api/push/key")
def push_key():
    if not notifier.enabled:
        raise HTTPException(503, "push notifications are not set up on this server")
    return {"key": notifier.public_key, "kinds": sorted(config.push_kinds)}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    if not notifier.enabled:
        raise HTTPException(503, "push notifications are not set up on this server")
    sub = valid_subscription(await request.json())
    if not sub:
        raise HTTPException(400, "not a push subscription")
    if not push_limiter.allow(voter_id(salt, client_ip(request))):
        raise HTTPException(429, "too many subscription changes; try again later")
    if store.add_push_subscription(sub, time.time()):
        notifier.confirm(sub)      # first time only; pages re-sync quietly on every load
    return {"ok": True}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    body = await request.json()
    endpoint = body.get("endpoint") if isinstance(body, dict) else None
    if isinstance(endpoint, str):
        store.remove_push_subscription(endpoint)
    return {"ok": True}


@app.get("/api/aircraft")
def aircraft_now():
    if not aircraft:
        return {"updated_at": None, "source": None, "error": "ADS-B disabled", "aircraft": []}
    return aircraft.snapshot()


@app.get("/live.mp3")
async def live_mp3(request: Request):
    assert live is not None
    q = live.fanout.subscribe()

    async def gen():
        try:
            while not await request.is_disconnected():
                try:
                    yield await asyncio.wait_for(q.get(), timeout=5)
                except asyncio.TimeoutError:
                    continue
        finally:
            live.fanout.unsubscribe(q)

    return StreamingResponse(gen(), media_type="audio/mpeg",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/events")
async def sse(request: Request):
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    yield await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class WebFiles(StaticFiles):
    """UI assets revalidate on every load so a new deploy shows up without a hard refresh."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", WebFiles(directory=WEB_DIR), name="web")
