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


PIPER_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_path() -> Path:
    return settings.piper_voice_dir / f"{settings.piper_voice}.onnx"


def voice_url(voice: str, suffix: str) -> str:
    """Hugging Face download URL for a voice file (.onnx or .onnx.json)."""
    parts = voice.split("-")
    if len(parts) < 3:
        raise ValueError(f"Voice names look like 'en_GB-jenny_dioco-medium', got {voice!r}")
    lang_region, name, quality = parts[0], "-".join(parts[1:-1]), parts[-1]
    lang = lang_region.split("_")[0]
    return f"{PIPER_BASE_URL}/{lang}/{lang_region}/{name}/{quality}/{voice}{suffix}"


def download_voice(voice: str, dest_dir: Path) -> Path:
    """Download a Piper voice (.onnx + .onnx.json) from Hugging Face."""
    import httpx

    dest_dir.mkdir(parents=True, exist_ok=True)
    onnx_file = dest_dir / f"{voice}.onnx"
    json_file = dest_dir / f"{voice}.onnx.json"

    for suffix, target in ((".onnx.json", json_file), (".onnx", onnx_file)):
        if target.exists() and target.stat().st_size > 0:
            continue
        url = voice_url(voice, suffix)
        log.info("Downloading Piper voice file: %s", url)
        tmp = target.with_suffix(target.suffix + ".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
            if resp.status_code == 404:
                raise RuntimeError(
                    f"Piper voice {voice!r} not found at {url}. "
                    "Check the voice name at https://rhasspy.github.io/piper-samples/"
                )
            resp.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 16):
                    f.write(chunk)
        tmp.rename(target)
        log.info("Downloaded %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return onnx_file


def ensure_voice(voice: str | None = None, dest_dir: Path | None = None) -> Path:
    """Ensure the voice (.onnx and .onnx.json) exists, downloading if necessary."""
    v = voice or settings.piper_voice
    d = dest_dir or settings.piper_voice_dir
    onnx_path = d / f"{v}.onnx"
    json_path = d / f"{v}.onnx.json"
    if not onnx_path.exists() or not json_path.exists():
        log.info("Piper voice %s not found at %s; downloading...", v, d)
        download_voice(v, d)
    return onnx_path


def load_piper():
    """Load (once) and return the Piper voice, downloading if missing. Thread-safe."""
    global _piper_voice
    with _piper_lock:
        if _piper_voice is None:
            from piper import PiperVoice  # imported lazily so gTTS-only setups don't need it

            try:  # hide harmless onnxruntime warnings (e.g. telemetry ID can't be saved
                import onnxruntime  # under systemd's read-only home); errors still show

                onnxruntime.set_default_logger_severity(3)
            except Exception:  # noqa: BLE001
                pass

            path = ensure_voice(settings.piper_voice, settings.piper_voice_dir)
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
