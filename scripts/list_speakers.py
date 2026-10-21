"""List Google Cast devices (Nest speakers, Chromecasts) visible on the network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homenotifier.speaker import list_speakers  # noqa: E402

if __name__ == "__main__":
    devices = list_speakers()
    if not devices:
        print("No cast devices found. Is the Pi on the same network/VLAN as the speaker?")
    for name, model, host in devices:
        print(f"{name!r:35} {model:25} {host}")
