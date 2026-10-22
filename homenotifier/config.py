"""Configuration loaded from environment variables (or a local .env file)."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root for manual runs. Under systemd the same file
# is supplied via EnvironmentFile=, and existing env vars are never overridden.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    value = _str(name)
    return int(value) if value else default


def _float(name: str, default: float | None) -> float | None:
    value = _str(name)
    return float(value) if value else default


def _bool(name: str, default: bool = False) -> bool:
    value = _str(name).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _path(name: str, default: str) -> Path:
    p = Path(_str(name, default)).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def detect_lan_ip() -> str:
    """Best-effort discovery of this machine's LAN IP (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@dataclass
class Settings:
    # --- Server ---
    host: str = field(default_factory=lambda: _str("HN_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("HN_PORT", 8765))
    auth_token: str = field(default_factory=lambda: _str("HN_AUTH_TOKEN"))
    # URL the speaker uses to fetch the generated MP3, e.g. http://192.168.1.50:8765
    public_base_url: str = field(default_factory=lambda: _str("HN_PUBLIC_BASE_URL"))
    audio_dir: Path = field(default_factory=lambda: _path("HN_AUDIO_DIR", "audio_cache"))
    log_level: str = field(default_factory=lambda: _str("HN_LOG_LEVEL", "INFO"))

    # --- Arrival behaviour ---
    owner_name: str = field(default_factory=lambda: _str("HN_OWNER_NAME", "there"))
    cooldown_minutes: int = field(default_factory=lambda: _int("HN_COOLDOWN_MINUTES", 30))
    arrival_delay_seconds: int = field(default_factory=lambda: _int("HN_ARRIVAL_DELAY_SECONDS", 20))
    # e.g. "22:00-07:00" - no announcements in this window. Empty = disabled.
    quiet_hours: str = field(default_factory=lambda: _str("HN_QUIET_HOURS"))
    timezone: str = field(default_factory=lambda: _str("HN_TIMEZONE"))

    # --- Google Calendar ---
    calendar_id: str = field(default_factory=lambda: _str("GOOGLE_CALENDAR_ID", "primary"))
    calendar_event_count: int = field(default_factory=lambda: _int("GOOGLE_CALENDAR_EVENT_COUNT", 3))
    # Option A: service account JSON key (recommended for a headless Pi)
    google_service_account_file: str = field(default_factory=lambda: _str("GOOGLE_SERVICE_ACCOUNT_FILE"))
    # Option B: OAuth desktop client + saved token
    google_oauth_client_file: Path = field(
        default_factory=lambda: _path("GOOGLE_OAUTH_CLIENT_FILE", "secrets/client_secret.json")
    )
    google_token_file: Path = field(default_factory=lambda: _path("GOOGLE_TOKEN_FILE", "secrets/token.json"))

    # --- Weather (Open-Meteo, no API key needed) ---
    latitude: float | None = field(default_factory=lambda: _float("WEATHER_LATITUDE", None))
    longitude: float | None = field(default_factory=lambda: _float("WEATHER_LONGITUDE", None))
    location_name: str = field(default_factory=lambda: _str("WEATHER_LOCATION_NAME"))

    # --- Ollama ---
    ollama_url: str = field(default_factory=lambda: _str("OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _str("OLLAMA_MODEL", "qwen2.5:1.5b"))
    ollama_timeout: int = field(default_factory=lambda: _int("OLLAMA_TIMEOUT_SECONDS", 90))
    # How long Ollama keeps the model in RAM after a request ("0" frees it immediately).
    ollama_keep_alive: str = field(default_factory=lambda: _str("OLLAMA_KEEP_ALIVE", "5m"))
    # For qwen3 models set to "false" to disable the slow <think> phase. Empty = don't send.
    ollama_think: str = field(default_factory=lambda: _str("OLLAMA_THINK"))

    # --- Speaker / TTS ---
    speaker_name: str = field(default_factory=lambda: _str("SPEAKER_NAME"))
    speaker_ip: str = field(default_factory=lambda: _str("SPEAKER_IP"))
    speaker_volume: float | None = field(default_factory=lambda: _float("SPEAKER_VOLUME", None))
    tts_lang: str = field(default_factory=lambda: _str("TTS_LANG", "en"))
    tts_tld: str = field(default_factory=lambda: _str("TTS_TLD", "com.au"))

    # --- Optional Wi-Fi presence detection (no phone app needed) ---
    presence_phone_ip: str = field(default_factory=lambda: _str("PRESENCE_PHONE_IP"))
    presence_interval_seconds: int = field(default_factory=lambda: _int("PRESENCE_INTERVAL_SECONDS", 30))
    presence_away_minutes: int = field(default_factory=lambda: _int("PRESENCE_AWAY_MINUTES", 20))

    def __post_init__(self) -> None:
        self.public_base_url = self._normalise_base_url(self.public_base_url)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    def _normalise_base_url(self, url: str) -> str:
        """Accept '192.168.1.5', '192.168.1.5:8765' or a full URL; always return scheme://host:port."""
        url = url.strip().rstrip("/")
        if not url:
            return f"http://{detect_lan_ip()}:{self.port}"
        if "://" not in url:
            url = f"http://{url}"
        scheme, rest = url.split("://", 1)
        host = rest.split("/", 1)[0]
        if ":" not in host:  # no port given - use the server's port
            url = url.replace(f"{scheme}://{host}", f"{scheme}://{host}:{self.port}", 1)
        return url


settings = Settings()
