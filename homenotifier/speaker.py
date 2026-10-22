"""Text-to-speech and casting to a Google Nest / Home speaker (or any Chromecast).

TTS engines:
  * piper (default) - local neural voice. ~60 MB model, ~100 MB RAM, renders a
    30 s briefing in a few seconds on a Pi 5. Supports real pauses between sections.
  * gtts - Google Translate voice. Needs internet; pauses are approximated.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

import pychromecast

from .config import settings

log = logging.getLogger(__name__)

MAX_CACHED_FILES = 20
CONTENT_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav"}


@dataclass(frozen=True)
class Pause:
    """A stretch of silence in a speech script."""

    ms: int


# A script is a sequence of things to say and pauses between them.
Script = list[str | Pause]


# --------------------------------------------------------------------------- #
# Piper
# --------------------------------------------------------------------------- #
_piper_voice = None
_piper_lock = threading.Lock()


def _voice_path() -> Path:
    return settings.piper_voice_dir / f"{settings.piper_voice}.onnx"


def load_piper():
    """Load (once) and return the Piper voice. Thread-safe."""
    global _piper_voice
    with _piper_lock:
        if _piper_voice is None:
            from piper import PiperVoice  # imported lazily so gTTS-only setups don't need it

            path = _voice_path()
            if not path.exists():
                raise RuntimeError(
                    f"Piper voice not found at {path}. "
                    f"Run: python scripts/download_voice.py {settings.piper_voice}"
                )
            t0 = time.monotonic()
            _piper_voice = PiperVoice.load(str(path))
            log.info("Loaded Piper voice %s in %.1f s", settings.piper_voice, time.monotonic() - t0)
        return _piper_voice


def _piper_syn_config():
    try:
        from piper import SynthesisConfig
    except ImportError:
        from piper.config import SynthesisConfig
    return SynthesisConfig(length_scale=settings.piper_length_scale)


def _piper_render(text: str) -> tuple[bytes, int, int, int]:
    """Render one text segment -> (pcm_frames, sample_rate, sample_width, channels)."""
    voice = load_piper()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        voice.synthesize_wav(text, wf, syn_config=_piper_syn_config())
    buf.seek(0)
    with wave.open(buf, "rb") as rf:
        return rf.readframes(rf.getnframes()), rf.getframerate(), rf.getsampwidth(), rf.getnchannels()


def _synthesize_piper(script: Script, path: Path) -> None:
    rendered: list[bytes | Pause] = []
    fmt: tuple[int, int, int] | None = None
    for item in script:
        if isinstance(item, Pause):
            rendered.append(item)
        elif item.strip():
            pcm, rate, width, channels = _piper_render(item)
            fmt = fmt or (rate, width, channels)
            rendered.append(pcm)

    if fmt is None:
        raise RuntimeError("Nothing to say")
    rate, width, channels = fmt
    bytes_per_ms = rate * width * channels / 1000

    with wave.open(str(path), "wb") as out:
        out.setframerate(rate)
        out.setsampwidth(width)
        out.setnchannels(channels)
        for chunk in rendered:
            if isinstance(chunk, Pause):
                n = int(chunk.ms * bytes_per_ms) // (width * channels) * (width * channels)
                out.writeframes(b"\x00" * n)
            else:
                out.writeframes(chunk)


# --------------------------------------------------------------------------- #
# gTTS
# --------------------------------------------------------------------------- #
def _synthesize_gtts(script: Script, path: Path) -> None:
    from gtts import gTTS

    # gTTS has no silence control; a sentence break plus ellipsis gives a short pause.
    parts: list[str] = []
    for item in script:
        if isinstance(item, Pause):
            if parts and item.ms >= 300:
                parts.append("...")
        elif item.strip():
            parts.append(item.strip())
    text = " ".join(parts).strip(" .") or "Hello"
    gTTS(text=text, lang=settings.tts_lang, tld=settings.tts_tld).save(str(path))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def synthesize(script: Script | str) -> Path:
    """Render a script (or plain text) to an audio file in the cache and return its path."""
    if isinstance(script, str):
        script = [script]
    script = [Pause(settings.tts_leading_silence_ms), *script]
    stem = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"

    if settings.tts_engine == "piper":
        path = settings.audio_dir / f"{stem}.wav"
        try:
            t0 = time.monotonic()
            _synthesize_piper(script, path)
            log.info("Piper rendered %s in %.1f s", path.name, time.monotonic() - t0)
            _prune_cache()
            return path
        except Exception as exc:  # noqa: BLE001 - fall back rather than stay silent
            path.unlink(missing_ok=True)
            log.warning("Piper TTS failed (%s); falling back to gTTS", exc)

    path = settings.audio_dir / f"{stem}.mp3"
    _synthesize_gtts(script, path)
    _prune_cache()
    return path


def _prune_cache() -> None:
    files = [p for p in settings.audio_dir.iterdir() if p.suffix in CONTENT_TYPES]
    files.sort(key=lambda p: p.stat().st_mtime)
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


def play_url(url: str, content_type: str = "audio/mpeg") -> None:
    """Blocking: cast an audio URL to the configured speaker."""
    cast, browser = _find_cast()
    try:
        cast.wait(timeout=15)
        if settings.speaker_volume is not None:
            cast.set_volume(max(0.0, min(1.0, settings.speaker_volume)))
        mc = cast.media_controller
        mc.play_media(url, content_type, title="HomeNotifier")
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


def speak(script: Script | str) -> None:
    """Blocking: synthesize a script (or plain text) and play it on the speaker."""
    path = synthesize(script)
    play_url(f"{settings.public_base_url}/audio/{path.name}", CONTENT_TYPES[path.suffix])


def list_speakers() -> list[tuple[str, str, str]]:
    casts, browser = pychromecast.get_chromecasts(timeout=10)
    browser.stop_discovery()
    return [(c.cast_info.friendly_name, c.cast_info.model_name, c.cast_info.host) for c in casts]
