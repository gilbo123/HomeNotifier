"""Assemble calendar + weather + LLM into a spoken arrival briefing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from . import calendar_client, llm, speaker, weather
from .config import settings
from .speaker import Pause, Script
from .timeutil import now

log = logging.getLogger(__name__)

WEATHER_HEADING = "Weather."
CALENDAR_HEADING = "Calendar."


@dataclass
class Briefing:
    greeting: str
    weather: str = ""  # spoken weather section ("" = skip)
    calendar: str = ""  # spoken calendar section ("" = skip)
    events: list[str] = field(default_factory=list)
    used_llm: bool = True

    def script(self) -> Script:
        """Greeting, then each section as: heading, short pause, body, longer pause."""
        heading_pause = Pause(settings.tts_heading_pause_ms)
        section_pause = Pause(settings.tts_section_pause_ms)
        script: Script = [self.greeting]
        for heading, body in ((WEATHER_HEADING, self.weather), (CALENDAR_HEADING, self.calendar)):
            if body:
                script += [section_pause, heading, heading_pause, body]
        return script

    @property
    def message(self) -> str:
        """Readable text version of the script (pauses shown as ' ... ')."""
        return " ".join("..." if isinstance(i, Pause) else i for i in self.script())


def _time_of_day() -> str:
    hour = now().hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _template_calendar(events: list[str]) -> str:
    if not events:
        return "Your calendar is clear."
    if len(events) == 1:
        return f"Your next event is {events[0]}."
    return f"Your next event is {events[0]}. " + " ".join(f"Then {e}." for e in events[1:])


async def build_briefing() -> Briefing:
    today = now().date()

    weather_task = weather.get_weather()
    events_task = asyncio.to_thread(calendar_client.get_upcoming_events)
    weather_res, events_res = await asyncio.gather(weather_task, events_task, return_exceptions=True)

    if isinstance(weather_res, Exception):
        log.warning("Weather unavailable: %s", weather_res)
        weather_facts = None
    else:
        weather_facts = weather_res.spoken()

    if isinstance(events_res, Exception):
        log.warning("Calendar unavailable: %s", events_res)
        events: list[str] | None = None
    else:
        events = [e.spoken(today) for e in events_res]

    # Deterministic versions - used if the LLM fails or leaves a section empty.
    template = {
        "greeting": f"Welcome home, {settings.owner_name}. Good {_time_of_day()}.",
        "weather": weather_facts or "",
        "calendar": _template_calendar(events) if events is not None else "",
    }

    prompt = llm.build_prompt(settings.owner_name, _time_of_day(), weather_facts or "unavailable", events)
    try:
        sections = await llm.generate_sections(prompt)
        used_llm = True
    except Exception as exc:  # noqa: BLE001 - always say *something*
        log.warning("LLM failed (%s); using template message", exc)
        sections, used_llm = {}, False

    def pick(name: str) -> str:
        if not template[name]:  # facts unavailable -> skip the section entirely
            return ""
        return sections.get(name) or template[name]

    return Briefing(
        greeting=pick("greeting"),
        weather=pick("weather"),
        calendar=pick("calendar"),
        events=events or [],
        used_llm=used_llm,
    )


async def announce() -> Briefing:
    briefing = await build_briefing()
    await asyncio.to_thread(speaker.speak, briefing.script())
    return briefing
