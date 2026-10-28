"""Download a Piper voice (.onnx + .onnx.json) from Hugging Face.

Usage:
    python scripts/download_voice.py                         # voice from PIPER_VOICE in .env
    python scripts/download_voice.py en_GB-alan-medium       # a specific voice

Browse and listen to voices at https://rhasspy.github.io/piper-samples/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homenotifier import speaker  # noqa: E402
from homenotifier.config import settings  # noqa: E402


def main() -> None:
    voice = sys.argv[1] if len(sys.argv) > 1 else settings.piper_voice
    try:
        speaker.download_voice(voice, settings.piper_voice_dir)
        if voice != settings.piper_voice:
            print(f"\nSet PIPER_VOICE={voice} in .env and restart the server to use it.")
    except Exception as exc:
        sys.exit(f"Failed to download {voice}: {exc}")


if __name__ == "__main__":
    main()
