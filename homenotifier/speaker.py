"""Text-to-speech and casting to a Google Nest / Home speaker (or any Chromecast)."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

import pychromecast
from gtts import gTTS

from .config import settings

log = logging.getLogger(__name__)

MAX_CACHED_FILES = 20


def synthesize(text: str) -> Path:
    """Render text to an MP3 in the audio cache and return its path."""
    path = settings.audio_dir / f"{int(time.time())}-{uuid.uuid4().hex[:8]}.mp3"
    gTTS(text=text, lang=settings.tts_lang, tld=settings.tts_tld).save(str(path))
    _prune_cache()
    return path


def _prune_cache() -> None:
    files = sorted(settings.audio_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    for old in files[:-MAX_CACHED_FILES]:
        old.unlink(missing_ok=True)


def _find_cast() -> tuple[pychromecast.Chromecast, object]:
    if not settings.speaker_name and not settings.speaker_ip:
        raise RuntimeError("Set SPEAKER_NAME and/or SPEAKER_IP")

    known_hosts = [settings.speaker_ip] if settings.speaker_ip else None
    if settings.speaker_name:
        casts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=[settings.speaker_name], known_hosts=known_hosts, timeout=15
        )
    else:
        casts, browser = pychromecast.get_chromecasts(known_hosts=known_hosts, timeout=15)
        casts = [c for c in casts if c.cast_info.host == settings.speaker_ip]

    if not casts:
        browser.stop_discovery()
        raise RuntimeError(
            f"Speaker not found (name={settings.speaker_name!r}, ip={settings.speaker_ip!r}). "
            "Run scripts/list_speakers.py to see what is visible."
        )
    return casts[0], browser


def play_url(url: str) -> None:
    """Blocking: cast an audio URL to the configured speaker."""
    cast, browser = _find_cast()
    try:
        cast.wait(timeout=15)
        if settings.speaker_volume is not None:
            cast.set_volume(max(0.0, min(1.0, settings.speaker_volume)))
        mc = cast.media_controller
        mc.play_media(url, "audio/mpeg", title="HomeNotifier")
        mc.block_until_active(timeout=15)
        log.info("Casting %s to %s", url, cast.cast_info.friendly_name)
        _wait_for_playback(mc, url)
    finally:
        # Playback continues on the speaker after we disconnect.
        browser.stop_discovery()
        cast.disconnect(timeout=5)


def _wait_for_playback(mc, url: str, timeout: float = 10.0) -> None:
    """Raise if the speaker reports it couldn't load the media (e.g. URL unreachable)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = mc.status
        if status.player_state in ("PLAYING", "BUFFERING"):
            return
        if status.player_state == "IDLE" and status.idle_reason == "ERROR":
            raise RuntimeError(
                f"Speaker could not load {url}. Check HN_PUBLIC_BASE_URL and that the "
                "speaker can reach the Pi on that port (firewall?)."
            )
        time.sleep(0.25)
    log.warning("Speaker never reported PLAYING for %s", url)


def speak(text: str) -> None:
    """Blocking: synthesize text and play it on the speaker."""
    path = synthesize(text)
    play_url(f"{settings.public_base_url}/audio/{path.name}")


def list_speakers() -> list[tuple[str, str, str]]:
    casts, browser = pychromecast.get_chromecasts(timeout=10)
    browser.stop_discovery()
    return [(c.cast_info.friendly_name, c.cast_info.model_name, c.cast_info.host) for c in casts]
