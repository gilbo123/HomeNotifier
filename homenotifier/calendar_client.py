"""Fetch upcoming Google Calendar events.

Supports two auth methods:
  * Service account (GOOGLE_SERVICE_ACCOUNT_FILE) - share your calendar with the
    service account's email address. Never expires; best for a headless Pi.
  * OAuth desktop client (secrets/client_secret.json + secrets/token.json) -
    create token.json with `python scripts/authorize_google.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .config import settings
from .timeutil import local_tz, now, spoken_day, spoken_time

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


@dataclass
class CalendarEvent:
    summary: str
    start: datetime | date
    all_day: bool
    location: str = ""

    def spoken(self, today: date) -> str:
        if self.all_day:
            when = spoken_day(self.start, today)
            text = f"{self.summary}, all day {when}"
        else:
            when = spoken_day(self.start.date(), today)
            text = f"{self.summary}, {when} at {spoken_time(self.start)}"
        if self.location:
            text += f" at {self.location.split(',')[0]}"
        return text


def _credentials():
    if settings.google_service_account_file:
        return service_account.Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )

    token_file = settings.google_token_file
    if not token_file.exists():
        raise RuntimeError(
            f"No Google token at {token_file}. Run scripts/authorize_google.py "
            "or configure GOOGLE_SERVICE_ACCOUNT_FILE."
        )
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
        else:
            raise RuntimeError("Google token is invalid; re-run scripts/authorize_google.py")
    return creds


def get_upcoming_events(count: int | None = None) -> list[CalendarEvent]:
    """Blocking call - run in a thread from async code."""
    count = count or settings.calendar_event_count
    service = build("calendar", "v3", credentials=_credentials(), cache_discovery=False)
    result = (
        service.events()
        .list(
            calendarId=settings.calendar_id,
            timeMin=now().isoformat(),
            maxResults=count,
            singleEvents=True,  # expand recurring events
            orderBy="startTime",
        )
        .execute()
    )

    tz = local_tz()
    events: list[CalendarEvent] = []
    for item in result.get("items", []):
        start = item.get("start", {})
        if "dateTime" in start:
            start_value: datetime | date = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
            all_day = False
        else:
            start_value = date.fromisoformat(start["date"])
            all_day = True
        events.append(
            CalendarEvent(
                summary=item.get("summary", "Untitled event"),
                start=start_value,
                all_day=all_day,
                location=item.get("location", ""),
            )
        )
    log.info("Fetched %d calendar events", len(events))
    return events
