"""Live-data tools: traffic flow, active incidents, weather, typical conditions.

All SQL is parameterised; tools return compact dicts that become numbered
evidence items (with timestamps) for generation, plus map features/cards.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo import haversine_m
from app.ingestion.geocoding import GeoMatch
from app.ingestion.sources.weather import ZONES, describe
from app.rag.retrieval.filters import query_geometry
from app.timeutil import IST, fmt_ist


def congestion_level(ratio: float | None) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= 0.75:
        return "free"
    if ratio >= 0.5:
        return "moderate"
    if ratio >= 0.3:
        return "heavy"
    return "severe"


async def _segments_near(
    session: AsyncSession, locations: list[GeoMatch], road_names: list[str]
) -> list[str]:
    geom, radius = query_geometry(locations)
    ids: list[str] = []
    if geom is not None:
        rows = await session.execute(
            text(
                "SELECT id FROM segments WHERE ST_DWithin(geom::geography, ST_GeomFromText(:g,4326)::geography, :r) "
                "ORDER BY ST_Distance(geom::geography, ST_GeomFromText(:g,4326)::geography) LIMIT 8"
            ),
            {"g": geom.wkt, "r": max(radius, 1500)},
        )
        ids += [r.id for r in rows]
    if road_names:
        rows = await session.execute(
            text("SELECT id FROM segments WHERE lower(road) = ANY(:names) LIMIT 12"),
            {"names": [n.lower() for n in road_names]},
        )
        ids += [r.id for r in rows]
    return list(dict.fromkeys(ids))


async def get_live_traffic(
    session: AsyncSession,
    locations: list[GeoMatch],
    now: datetime,
    segment_ids: list[str] | None = None,
) -> dict[str, Any]:
    roads = [m.name for m in locations if m.type == "road"]
    ids = segment_ids or await _segments_near(session, locations, roads)
    if not ids:
        return {"available": False, "reason": "no monitored segment near the requested location"}
    rows = (
        await session.execute(
            text(
                """
                WITH latest AS (
                  SELECT DISTINCT ON (o.segment_id) o.segment_id, o.observed_at, o.current_speed,
                         o.free_flow_speed, o.road_closure
                  FROM traffic_flow_obs o
                  WHERE o.segment_id = ANY(:ids) AND o.observed_at > :since
                  ORDER BY o.segment_id, o.observed_at DESC
                ), trend AS (
                  SELECT segment_id, avg(current_speed / NULLIF(free_flow_speed,0)) AS ratio_60
                  FROM traffic_flow_obs
                  WHERE segment_id = ANY(:ids) AND observed_at BETWEEN :since60 AND :now
                  GROUP BY segment_id
                )
                SELECT s.id, s.name, s.road, s.lat, s.lon, ST_AsGeoJSON(s.geom)::json AS geojson,
                       l.observed_at, l.current_speed, l.free_flow_speed, l.road_closure, t.ratio_60
                FROM segments s JOIN latest l ON l.segment_id = s.id
                LEFT JOIN trend t ON t.segment_id = s.id
                """
            ),
            {
                "ids": ids,
                "since": now - timedelta(hours=3),
                "since60": now - timedelta(minutes=60),
                "now": now,
            },
        )
    ).all()
    if not rows:
        return {
            "available": False,
            "reason": "no recent TomTom observations (key missing or polling paused)",
            "segments_checked": ids,
        }
    segs = []
    for r in rows:
        ratio = r.current_speed / r.free_flow_speed if r.free_flow_speed else None
        trend = None
        if r.ratio_60 is not None and ratio is not None:
            diff = ratio - float(r.ratio_60)
            trend = "improving" if diff > 0.07 else "worsening" if diff < -0.07 else "steady"
        segs.append(
            {
                "segment_id": r.id,
                "name": r.name,
                "road": r.road,
                "lat": r.lat,
                "lon": r.lon,
                "geometry": r.geojson,
                "current_speed_kmph": round(r.current_speed),
                "free_flow_speed_kmph": round(r.free_flow_speed),
                "congestion_ratio": round(ratio, 2) if ratio is not None else None,
                "level": "closed" if r.road_closure else congestion_level(ratio),
                "trend_60min": trend,
                "as_of": fmt_ist(r.observed_at),
            }
        )
    segs.sort(key=lambda s: s["congestion_ratio"] if s["congestion_ratio"] is not None else 9)
    return {"available": True, "segments": segs, "as_of": max(s["as_of"] for s in segs)}


async def get_active_incidents(
    session: AsyncSession, locations: list[GeoMatch], radius_m: float | None = None
) -> dict[str, Any]:
    geom, radius = query_geometry(locations)
    params: dict[str, Any] = {}
    where = "is_active"
    if geom is not None:
        where += " AND geom IS NOT NULL AND ST_DWithin(geom::geography, ST_GeomFromText(:g,4326)::geography, :r)"
        params = {"g": geom.wkt, "r": radius_m or max(radius, 2000)}
    rows = (
        await session.execute(
            text(
                f"""SELECT id, category, description, from_name, to_name, road_numbers, delay_seconds,
                           magnitude, start_time, last_seen_at, ST_AsGeoJSON(geom)::json AS geojson
                    FROM traffic_incidents WHERE {where}
                    ORDER BY COALESCE(delay_seconds, 0) DESC LIMIT 15"""
            ),
            params,
        )
    ).all()
    return {
        "available": True,
        "count": len(rows),
        "incidents": [
            {
                "id": r.id,
                "category": r.category,
                "description": r.description,
                "from": r.from_name,
                "to": r.to_name,
                "roads": r.road_numbers,
                "delay_min": round(r.delay_seconds / 60) if r.delay_seconds else None,
                "since": fmt_ist(r.start_time) if r.start_time else None,
                "as_of": fmt_ist(r.last_seen_at),
                "geometry": r.geojson,
            }
            for r in rows
        ],
    }


def nearest_zone(lat: float, lon: float) -> str:
    return min(ZONES, key=lambda z: haversine_m(lat, lon, *ZONES[z]))


async def get_weather(
    session: AsyncSession, locations: list[GeoMatch], start: datetime, end: datetime, now: datetime
) -> dict[str, Any]:
    zone = nearest_zone(locations[0].lat, locations[0].lon) if locations else "central"
    rows = (
        await session.execute(
            text(
                """SELECT observed_at, kind, temperature, rain, precipitation, visibility, weather_code, flags
                   FROM weather_obs WHERE zone = :z AND (
                     (kind = 'current' AND observed_at > CAST(:now AS timestamptz) - interval '3 hours') OR
                     (observed_at BETWEEN :s AND :e))
                   ORDER BY observed_at"""
            ),
            {"z": zone, "s": start - timedelta(hours=1), "e": end, "now": now},
        )
    ).all()
    if not rows:
        return {"available": False, "zone": zone, "reason": "no weather observations yet"}
    current = next((r for r in reversed(rows) if r.kind == "current"), rows[-1])
    window_flags = sorted({f for r in rows for f in (r.flags or [])})
    max_rain = max((r.rain or 0) for r in rows)
    return {
        "available": True,
        "zone": zone,
        "as_of": fmt_ist(current.observed_at),
        "current": {
            "temperature_c": current.temperature,
            "rain_mm": current.rain,
            "visibility_m": current.visibility,
            "conditions": describe(current.weather_code),
        },
        "window_flags": window_flags,
        "max_rain_mm_per_h": max_rain,
    }


async def typical_conditions(
    session: AsyncSession,
    locations: list[GeoMatch],
    weekday: int,
    hour: int,
    segment_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Historical mean congestion for weekday (0=Mon) × hour from flow observations."""
    roads = [m.name for m in locations if m.type == "road"]
    ids = segment_ids or await _segments_near(session, locations, roads)
    if not ids:
        return {"available": False, "reason": "no monitored segment nearby"}
    rows = (
        await session.execute(
            text(
                """SELECT s.id, s.name, avg(o.current_speed / NULLIF(o.free_flow_speed, 0)) AS ratio,
                          count(*) AS n
                   FROM traffic_flow_obs o JOIN segments s ON s.id = o.segment_id
                   WHERE o.segment_id = ANY(:ids)
                     AND extract(isodow FROM o.observed_at AT TIME ZONE 'Asia/Kolkata') = :dow
                     AND extract(hour FROM o.observed_at AT TIME ZONE 'Asia/Kolkata') = :hour
                   GROUP BY s.id, s.name HAVING count(*) >= 2"""
            ),
            {"ids": ids, "dow": weekday + 1, "hour": hour},
        )
    ).all()
    if not rows:
        return {
            "available": False,
            "reason": "not enough historical observations for that weekday/hour yet",
            "weekday": weekday,
            "hour": hour,
        }
    return {
        "available": True,
        "weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday],
        "hour": hour,
        "segments": [
            {
                "segment_id": r.id,
                "name": r.name,
                "typical_ratio": round(float(r.ratio), 2),
                "level": congestion_level(float(r.ratio)),
                "samples": int(r.n),
            }
            for r in rows
        ],
    }


def ist_weekday_hour(dt: datetime) -> tuple[int, int]:
    d = dt.astimezone(IST)
    return d.weekday(), d.hour
