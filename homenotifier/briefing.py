"""Assemble calendar + weather + LLM into a spoken arrival briefing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from . import calendar_client, llm, speaker, weather
from .config import settings
from .timeutil import now

log = logging.getLogger(__name__)


@dataclass
class Briefing:
    message: str
    weather: str
    events: list[str] = field(default_factory=list)
    used_llm: bool = True


def _time_of_day() -> str:
    hour = now().hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _fallback_message(name: str, weather_text: str, events: list[str], calendar_ok: bool) -> str:
    parts = [f"Welcome home, {name}. Good {_time_of_day()}."]
    if weather_text != "unavailable":
        parts.append(weather_text)
    if events:
        parts.append("Coming up: " + "; ".join(events) + ".")
    elif calendar_ok:
        parts.append("You have nothing coming up in your calendar.")
    return " ".join(parts)


async def build_briefing() -> Briefing:
    today = now().date()

    weather_task = weather.get_weather()
    events_task = asyncio.to_thread(calendar_client.get_upcoming_events)
    weather_res, events_res = await asyncio.gather(weather_task, events_task, return_exceptions=True)

    if isinstance(weather_res, Exception):
        log.warning("Weather unavailable: %s", weather_res)
        weather_text = "unavailable"
    else:
        weather_text = weather_res.spoken()

    if isinstance(events_res, Exception):
        log.warning("Calendar unavailable: %s", events_res)
        events: list[str] = []
        events_note = ["Calendar unavailable."]
    else:
        events = [e.spoken(today) for e in events_res]
        events_note = events

    prompt = llm.build_prompt(settings.owner_name, _time_of_day(), weather_text, events_note)
    try:
        message = await llm.generate_message(prompt)
        return Briefing(message=message, weather=weather_text, events=events)
    except Exception as exc:  # noqa: BLE001 - always say *something*
        log.warning("LLM failed (%s); using template message", exc)
        return Briefing(
            message=_fallback_message(
                settings.owner_name, weather_text, events, calendar_ok=not isinstance(events_res, Exception)
            ),
            weather=weather_text,
            events=events,
            used_llm=False,
        )


async def announce() -> Briefing:
    briefing = await build_briefing()
    await asyncio.to_thread(speaker.speak, briefing.message)
    return briefing
