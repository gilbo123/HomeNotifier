"""Download a Piper voice (.onnx + .onnx.json) from Hugging Face.

Usage:
    python scripts/download_voice.py                         # voice from PIPER_VOICE in .env
    python scripts/download_voice.py en_GB-alan-medium       # a specific voice

Browse and listen to voices at https://rhasspy.github.io/piper-samples/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from homenotifier.config import settings  # noqa: E402

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def voice_url(voice: str, suffix: str) -> str:
    # e.g. en_GB-jenny_dioco-medium -> en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx
    parts = voice.split("-")
    if len(parts) < 3:
        sys.exit(f"Voice names look like 'en_GB-jenny_dioco-medium', got {voice!r}")
    lang_region, name, quality = parts[0], "-".join(parts[1:-1]), parts[-1]
    lang = lang_region.split("_")[0]
    return f"{BASE_URL}/{lang}/{lang_region}/{name}/{quality}/{voice}{suffix}"


def download(url: str, dest: Path) -> None:
    print(f"Downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
        if resp.status_code == 404:
            sys.exit(f"Not found: {url}\nCheck the voice name at https://rhasspy.github.io/piper-samples/")
        resp.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in resp.iter_bytes(1 << 16):
                f.write(chunk)
    tmp.rename(dest)
    print(f"  -> {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    voice = sys.argv[1] if len(sys.argv) > 1 else settings.piper_voice
    settings.piper_voice_dir.mkdir(parents=True, exist_ok=True)
    for suffix in (".onnx.json", ".onnx"):
        download(voice_url(voice, suffix), settings.piper_voice_dir / f"{voice}{suffix}")
    if voice != settings.piper_voice:
        print(f"\nSet PIPER_VOICE={voice} in .env and restart the server to use it.")


if __name__ == "__main__":
    main()
