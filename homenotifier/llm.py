"""Turn the raw facts into short spoken sections using Ollama.

The model returns JSON with separate `greeting`, `weather` and `calendar`
fields so the speech can have clear headings and pauses between sections.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from .config import settings

log = logging.getLogger(__name__)

SECTIONS = ("greeting", "weather", "calendar")

SYSTEM_PROMPT = """You write short spoken welcome-home briefings for a smart speaker.
Reply with JSON containing three fields:
- "greeting": ONE short, warm sentence welcoming the person home by name. Do not mention weather or events.
- "weather": 1 to 2 sentences about today's weather, starting with "Right now" or "Today".
- "calendar": 1 to 3 sentences listing the events in order with their day and time,
  starting with "Your next event is" or "You have". If there are no events, say the calendar is clear.
Rules:
- Use ONLY the facts provided. Never invent events, times, places or weather.
- Plain spoken English: no lists, markdown, emojis or abbreviations like "km/h" (say "kilometres per hour").
- If a section's facts are marked unavailable, return an empty string for it."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {name: {"type": "string"} for name in SECTIONS},
    "required": list(SECTIONS),
}


def build_prompt(name: str, time_of_day: str, weather: str, events: list[str] | None) -> str:
    if events is None:
        events_text = "- unavailable"
    elif events:
        events_text = "\n".join(f"- {e}" for e in events)
    else:
        events_text = "- No upcoming events."
    return (
        f"Person's name: {name}\n"
        f"Time of day: {time_of_day}\n"
        f"Weather: {weather}\n"
        f"Next calendar events:\n{events_text}"
    )


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)  # qwen3 reasoning
    text = re.sub(r"[*_#`>]", "", text)  # stray markdown is read aloud by TTS
    return " ".join(text.split())


async def generate_sections(prompt: str) -> dict[str, str]:
    """Return {"greeting": ..., "weather": ..., "calendar": ...}; values may be empty."""
    payload: dict = {
        "model": settings.ollama_model,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "format": RESPONSE_SCHEMA,  # Ollama structured output
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0.4, "num_predict": 300},
    }
    if settings.ollama_think:
        payload["think"] = settings.ollama_think.lower() in {"1", "true", "yes", "on"}

    async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
        resp = await client.post(f"{settings.ollama_url}/api/generate", json=payload)
        if resp.status_code == 404:
            raise RuntimeError(f"Ollama model {settings.ollama_model!r} not found - run: ollama pull {settings.ollama_model}")
        resp.raise_for_status()
        raw = resp.json().get("response", "")

    try:
        data = json.loads(_clean_json(raw))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON: {raw[:200]!r}") from exc

    sections = {name: _clean(str(data.get(name, ""))) for name in SECTIONS}
    log.info("LLM sections: %s", sections)
    return sections


def _clean_json(raw: str) -> str:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start : end + 1] if start != -1 and end != -1 else raw
