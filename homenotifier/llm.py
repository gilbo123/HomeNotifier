"""Turn the raw facts into a short, natural spoken greeting using Ollama."""

from __future__ import annotations

import logging
import re

import httpx

from .config import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write short spoken welcome-home messages for a smart speaker.
Rules:
- Use ONLY the facts provided. Never invent events, times, places or weather.
- 3 to 5 short sentences, under 90 words total.
- Warm and casual. Plain text only: no lists, no markdown, no emojis.
- Mention the weather briefly, then the upcoming events in order with their times.
- If a fact is marked unavailable, skip it without apologising at length."""


def build_prompt(name: str, time_of_day: str, weather: str, events: list[str]) -> str:
    events_text = "\n".join(f"- {e}" for e in events) if events else "- No upcoming events."
    return (
        f"Person's name: {name}\n"
        f"Time of day: {time_of_day}\n"
        f"Weather: {weather}\n"
        f"Next calendar events:\n{events_text}\n\n"
        "Write the welcome-home message now."
    )


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)  # qwen3 reasoning
    text = re.sub(r"[*_#`>]", "", text)  # stray markdown is read aloud by TTS
    return " ".join(text.split())


async def generate_message(prompt: str) -> str:
    payload: dict = {
        "model": settings.ollama_model,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0.4, "num_predict": 200},
    }
    if settings.ollama_think:
        payload["think"] = settings.ollama_think.lower() in {"1", "true", "yes", "on"}

    async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
        resp = await client.post(f"{settings.ollama_url}/api/generate", json=payload)
        if resp.status_code == 404:
            raise RuntimeError(f"Ollama model {settings.ollama_model!r} not found - run: ollama pull {settings.ollama_model}")
        resp.raise_for_status()
        text = _clean(resp.json().get("response", ""))

    if not text:
        raise RuntimeError("Ollama returned an empty response")
    log.info("LLM message: %s", text)
    return text
