"""Current weather + today's forecast from Open-Meteo (free, no API key)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import settings

log = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> spoken description
WMO_CODES = {
    0: "clear skies",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy with frost",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "severe thunderstorms with hail",
}


@dataclass
class Weather:
    weather_code: int
    description: str
    temperature: float
    feels_like: float
    wind_kmh: float
    today_max: float
    today_min: float
    rain_chance: int

    @property
    def is_sunny_and_clear(self) -> bool:
        """True if skies are clear or mainly clear (WMO 0 or 1) and rain chance is low (< 20%)."""
        return self.weather_code in (0, 1) and self.rain_chance < 20

    def spoken(self) -> str:
        where = f" in {settings.location_name}" if settings.location_name else ""
        return (
            f"The weather right now is {self.description}{where} and {round(self.temperature)} degrees, "
            f"feeling like {round(self.feels_like)}, with wind at {round(self.wind_kmh)} kilometres per hour. "
            f"Today ranges from {round(self.today_min)} to {round(self.today_max)} degrees, "
            f"with a {self.rain_chance} percent chance of rain."
        )


async def get_weather() -> Weather:
    if settings.latitude is None or settings.longitude is None:
        raise RuntimeError("WEATHER_LATITUDE / WEATHER_LONGITUDE are not set")

    params = {
        "latitude": settings.latitude,
        "longitude": settings.longitude,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 1,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    cur, daily = data["current"], data["daily"]
    code = cur.get("weather_code", 0)
    weather = Weather(
        weather_code=code,
        description=WMO_CODES.get(code, "unsettled weather"),
        temperature=cur["temperature_2m"],
        feels_like=cur["apparent_temperature"],
        wind_kmh=cur["wind_speed_10m"],
        today_max=daily["temperature_2m_max"][0],
        today_min=daily["temperature_2m_min"][0],
        rain_chance=daily["precipitation_probability_max"][0] or 0,
    )
    log.info("Weather (code=%d, sunny_and_clear=%s): %s", code, weather.is_sunny_and_clear, weather.spoken())
    return weather
