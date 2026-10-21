"""Time helpers shared across modules."""

from __future__ import annotations

from datetime import date, datetime, time, tzinfo
from zoneinfo import ZoneInfo

from .config import settings


def local_tz() -> tzinfo:
    if settings.timezone:
        return ZoneInfo(settings.timezone)
    return datetime.now().astimezone().tzinfo  # system timezone


def now() -> datetime:
    return datetime.now(local_tz())


def spoken_time(t: datetime) -> str:
    """6:30 PM -> '6:30 PM', 7:00 PM -> '7 PM'."""
    hour = t.strftime("%I").lstrip("0") or "12"
    suffix = t.strftime("%p")
    return f"{hour} {suffix}" if t.minute == 0 else f"{hour}:{t.minute:02d} {suffix}"


def spoken_day(d: date, today: date) -> str:
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if 1 < delta < 7:
        return d.strftime("%A")
    return f"{d.strftime('%A')} the {_ordinal(d.day)} of {d.strftime('%B')}"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def in_quiet_hours(spec: str, at: datetime | None = None) -> bool:
    """spec like '22:00-07:00'. Handles windows that wrap past midnight."""
    if not spec:
        return False
    start_s, end_s = (part.strip() for part in spec.split("-", 1))
    start, end = time.fromisoformat(start_s), time.fromisoformat(end_s)
    current = (at or now()).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end
