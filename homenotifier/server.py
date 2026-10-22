"""FastAPI app: HTTP endpoints for the phone, plus the optional presence watcher."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__, briefing, presence, speaker
from .config import settings
from .timeutil import in_quiet_hours

log = logging.getLogger(__name__)

_announce_lock = asyncio.Lock()
_last_announced: float = 0.0  # monotonic seconds


async def handle_arrival(force: bool = False, delay: int | None = None) -> None:
    """Run the full arrival flow, honouring cooldown and quiet hours."""
    global _last_announced

    if _announce_lock.locked():
        log.info("Arrival ignored: an announcement is already in progress")
        return

    async with _announce_lock:
        if not force:
            since = time.monotonic() - _last_announced
            if _last_announced and since < settings.cooldown_minutes * 60:
                log.info("Arrival ignored: cooldown (%d s since last announcement)", since)
                return
            if in_quiet_hours(settings.quiet_hours):
                log.info("Arrival ignored: quiet hours (%s)", settings.quiet_hours)
                return

        wait = settings.arrival_delay_seconds if delay is None else delay
        if wait > 0:
            log.info("Arrival detected; announcing in %d s", wait)
            await asyncio.sleep(wait)

        try:
            result = await briefing.announce()
            _last_announced = time.monotonic()
            log.info("Announced (llm=%s): %s", result.used_llm, result.message)
        except Exception:
            log.exception("Arrival announcement failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.auth_token:
        log.warning("HN_AUTH_TOKEN is not set - endpoints are UNAUTHENTICATED")
    log.info("Audio will be served from %s/audio/", settings.public_base_url)

    if settings.tts_engine == "piper":
        # Load the voice in the background so the first announcement isn't delayed
        # and a missing voice file shows up in the logs straight away.
        async def _preload() -> None:
            try:
                await asyncio.to_thread(speaker.load_piper)
            except Exception as exc:  # noqa: BLE001
                log.warning("Piper voice not loaded (%s) - will fall back to gTTS", exc)

        asyncio.create_task(_preload())

    watcher = None
    if settings.presence_phone_ip:
        watcher = asyncio.create_task(presence.watch(handle_arrival))
    yield
    if watcher:
        watcher.cancel()


app = FastAPI(title="HomeNotifier", version=__version__, lifespan=lifespan)


def require_token(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
    token: str | None = Query(default=None, description="Alternative to headers"),
) -> None:
    if not settings.auth_token:
        return
    supplied = x_auth_token or token
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
    if not supplied or not secrets.compare_digest(supplied, settings.auth_token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.api_route("/arrive", methods=["GET", "POST"], status_code=202, dependencies=[Depends(require_token)])
async def arrive(
    force: bool = Query(False, description="Ignore cooldown and quiet hours"),
    delay: int | None = Query(None, ge=0, le=600, description="Override arrival delay (seconds)"),
) -> dict:
    """Called by the phone when it gets home. Returns immediately; speaks in the background."""
    asyncio.create_task(handle_arrival(force=force, delay=delay))
    return {"status": "accepted"}


@app.get("/preview", dependencies=[Depends(require_token)])
async def preview() -> dict:
    """Build the briefing text without speaking it - handy for testing."""
    b = await briefing.build_briefing()
    return {
        "message": b.message,
        "greeting": b.greeting,
        "weather": b.weather,
        "calendar": b.calendar,
        "events": b.events,
        "used_llm": b.used_llm,
    }


class SayRequest(BaseModel):
    text: str


@app.post("/say", dependencies=[Depends(require_token)])
async def say(req: SayRequest) -> dict:
    """Speak arbitrary text on the speaker."""
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    await asyncio.to_thread(speaker.speak, text[:1000])
    return {"status": "spoken"}


_AUDIO_NAME = re.compile(r"^[\w-]+\.(mp3|wav)$")


@app.api_route("/audio/{name}", methods=["GET", "HEAD"])
async def audio(name: str) -> FileResponse:
    """Served without auth because the speaker can't send headers; names are random."""
    path = settings.audio_dir / name
    if not _AUDIO_NAME.match(name) or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=speaker.CONTENT_TYPES[path.suffix])
