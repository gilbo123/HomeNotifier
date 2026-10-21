"""One-off Google OAuth authorisation - creates secrets/token.json.

Run this on any machine with a web browser (your laptop is easiest), then copy
secrets/token.json to the Pi. On the Pi itself over SSH, use:

    ssh -L 8085:localhost:8085 pi@raspberrypi.local
    python scripts/authorize_google.py --port 8085 --no-browser

and open the printed URL in your laptop's browser.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from homenotifier.calendar_client import SCOPES  # noqa: E402
from homenotifier.config import settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening a browser")
    args = parser.parse_args()

    client_file = settings.google_oauth_client_file
    if not client_file.exists():
        sys.exit(f"OAuth client file not found: {client_file}\nDownload it from Google Cloud Console (see README).")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(
        port=args.port,
        open_browser=not args.no_browser,
        access_type="offline",
        prompt="consent",  # guarantees a refresh token is issued
    )

    settings.google_token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.google_token_file.write_text(creds.to_json())
    settings.google_token_file.chmod(0o600)
    print(f"Saved token to {settings.google_token_file}")


if __name__ == "__main__":
    main()
