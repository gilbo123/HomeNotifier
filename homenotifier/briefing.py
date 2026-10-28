"""Assemble greeting + BYD battery + weather + calendar into a spoken arrival briefing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from . import byd, calendar_client, llm, speaker, weather
from .config import settings
from .speaker import Pause, Script
from .timeutil import now

log = logging.getLogger(__name__)


@dataclass
class Briefing:
    greeting: str
    battery_percent: float | None = None
    weather: str = ""  # spoken weather section ("" = skip)
    calendar: str = ""  # spoken calendar section ("" = skip)
    events: list[str] = field(default_factory=list)
    used_llm: bool = False

    def script(self) -> Script:
        """Construct the arrival script with the requested pause cadence:
        
        "Welcome home Gilbert!" [2 x pause]
        "Lord of the manor." [4 x pause]
        "King of the castle" [4 x pause]
        "Battery:" [pause] "BYD battery percentage is: {percent}" [2 x pause]
        (if not sunny and clear): "Weather:" [pause] "The weather ...." [2 x pause]
        "Calendar:" [pause] "Your next..."
        """
        p1 = Pause(settings.tts_heading_pause_ms)
        p2 = Pause(settings.tts_heading_pause_ms * 2)
        p4 = Pause(settings.tts_heading_pause_ms * 4)

        script: Script = [
            f"Welcome home {settings.owner_name}!",
            p2,
            "Lord of the manor.",
            p4,
            "King of the castle.",
            p4,
        ]

        if self.battery_percent is not None:
            script += [
                "Battery:",
                p1,
                f"BYD battery percentage is: {round(self.battery_percent)} percent",
                p2,
            ]

        if self.weather:
            script += [
                "Weather:",
                p1,
                self.weather,
                p2,
            ]

        if self.calendar:
            script += [
                "Calendar:",
                p1,
                self.calendar,
            ]

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

    battery_task = byd.get_battery_percentage()
    weather_task = weather.get_weather()
    events_task = asyncio.to_thread(calendar_client.get_upcoming_events)
    battery_res, weather_res, events_res = await asyncio.gather(
        battery_task, weather_task, events_task, return_exceptions=True
    )

    if isinstance(battery_res, Exception) or battery_res is None:
        if isinstance(battery_res, Exception):
            log.warning("BYD battery unavailable: %s", battery_res)
        battery_percent = None
    else:
        battery_percent = float(battery_res)

    weather_spoken = ""
    weather_facts = None
    if isinstance(weather_res, Exception):
        log.warning("Weather unavailable: %s", weather_res)
    else:
        weather_facts = weather_res.spoken()
        # Only announce weather if not sunny and clear
        if not weather_res.is_sunny_and_clear:
            weather_spoken = weather_facts
        else:
            log.info("Weather is sunny and clear; omitting weather section")

    if isinstance(events_res, Exception):
        log.warning("Calendar unavailable: %s", events_res)
        events: list[str] | None = None
        calendar_spoken = ""
    else:
        events = [e.spoken(today) for e in events_res]
        calendar_spoken = _template_calendar(events)

    # Optional LLM refinement for weather and calendar phrasing
    used_llm = False
    if weather_spoken or calendar_spoken:
        prompt = llm.build_prompt(
            settings.owner_name,
            _time_of_day(),
            weather_facts if weather_spoken else "unavailable",
            events if calendar_spoken else None,
        )
        try:
            sections = await llm.generate_sections(prompt)
            if weather_spoken and sections.get("weather"):
                weather_spoken = sections["weather"]
            if calendar_spoken and sections.get("calendar"):
                calendar_spoken = sections["calendar"]
            used_llm = True
        except Exception as exc:  # noqa: BLE001 - keep template phrasing
            log.warning("LLM failed (%s); using template message", exc)

    return Briefing(
        greeting=f"Welcome home {settings.owner_name}!",
        battery_percent=battery_percent,
        weather=weather_spoken,
        calendar=calendar_spoken,
        events=events or [],
        used_llm=used_llm,
    )


async def announce() -> Briefing:
    briefing = await build_briefing()
    await asyncio.to_thread(speaker.speak, briefing.script())
    return briefing
