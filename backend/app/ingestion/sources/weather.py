"""Open-Meteo weather (no key): current, 48 h forecast and 1 day history.

Derived flags: ``heavy_rain`` (≥ 7.5 mm/h), ``fog_low_visibility`` (< 1 km),
``extreme_heat`` (≥ 42 °C).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.ingestion.http import PoliteFetcher
from app.timeutil import IST

SOURCE_ID = "weather"
URL = "https://api.open-meteo.com/v1/forecast"

ZONES: dict[str, tuple[float, float]] = {
    "central": (28.6315, 77.2167),
    "new_delhi": (28.6129, 77.2295),
    "north": (28.6800, 77.2240),
    "south": (28.5245, 77.2066),
    "east": (28.6304, 77.2773),
    "west": (28.6219, 77.0878),
    "north_west": (28.7160, 77.1150),
    "south_west": (28.5921, 77.0460),
}
VARS = "temperature_2m,precipitation,rain,weather_code,visibility"

WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    80: "rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


@dataclass
class WeatherRow:
    zone: str
    lat: float
    lon: float
    observed_at: datetime
    kind: str
    temperature: float | None
    precipitation: float | None
    rain: float | None
    visibility: float | None
    weather_code: int | None
    flags: list[str] = field(default_factory=list)


def derive_flags(
    rain: float | None, visibility: float | None, temp: float | None, code: int | None
) -> list[str]:
    flags = []
    if (rain or 0) >= 7.5 or code in (65, 82, 95, 96, 99):
        flags.append("heavy_rain")
    if (visibility is not None and visibility < 1000) or code in (45, 48):
        flags.append("fog_low_visibility")
    if temp is not None and temp >= 42:
        flags.append("extreme_heat")
    return flags


def parse_open_meteo(payload: Any, now: datetime) -> list[WeatherRow]:
    locations = payload if isinstance(payload, list) else [payload]
    rows: list[WeatherRow] = []
    for zone, loc in zip(ZONES, locations, strict=False):
        lat, lon = ZONES[zone]
        cur = loc.get("current") or {}
        if cur:
            t = datetime.fromisoformat(cur["time"]).replace(tzinfo=IST)
            rows.append(
                WeatherRow(
                    zone,
                    lat,
                    lon,
                    t,
                    "current",
                    cur.get("temperature_2m"),
                    cur.get("precipitation"),
                    cur.get("rain"),
                    cur.get("visibility"),
                    cur.get("weather_code"),
                    derive_flags(
                        cur.get("rain"),
                        cur.get("visibility"),
                        cur.get("temperature_2m"),
                        cur.get("weather_code"),
                    ),
                )
            )
        hourly = loc.get("hourly") or {}
        for i, ts in enumerate(hourly.get("time", [])):
            t = datetime.fromisoformat(ts).replace(tzinfo=IST)
            vals = {k: (hourly.get(k) or [None] * (i + 1))[i] for k in VARS.split(",")}
            rows.append(
                WeatherRow(
                    zone,
                    lat,
                    lon,
                    t,
                    "forecast" if t > now else "history",
                    vals["temperature_2m"],
                    vals["precipitation"],
                    vals["rain"],
                    vals["visibility"],
                    vals["weather_code"],
                    derive_flags(
                        vals["rain"],
                        vals["visibility"],
                        vals["temperature_2m"],
                        vals["weather_code"],
                    ),
                )
            )
    return rows


async def fetch_weather(fetcher: PoliteFetcher, now: datetime) -> list[WeatherRow]:
    lats = ",".join(str(v[0]) for v in ZONES.values())
    lons = ",".join(str(v[1]) for v in ZONES.values())
    res = await fetcher.get(
        URL,
        params={
            "latitude": lats,
            "longitude": lons,
            "current": VARS,
            "hourly": VARS,
            "past_days": 1,
            "forecast_days": 2,
            "timezone": "Asia/Kolkata",
        },
        check_robots=False,
    )
    res_json = json.loads(res.text)
    return parse_open_meteo(res_json, now)


def describe(code: int | None) -> str:
    return WEATHER_CODES.get(code or 0, "unknown")
