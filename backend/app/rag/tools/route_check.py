"""check_route: what affects a corridor between two places at a given time."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.geo import buffer_m, haversine_m, to_geojson
from app.ingestion.geocoding import GeoMatch
from app.observability.logging import get_logger
from app.rag.tools.live import congestion_level, nearest_zone
from app.timeutil import fmt_ist

log = get_logger(__name__)
CORRIDOR_M = 500


async def route_line(origin: GeoMatch, dest: GeoMatch) -> tuple[LineString, str]:
    """OSRM driving route if configured, else a straight line."""
    settings = get_settings()
    if settings.osrm_url:
        url = f"{settings.osrm_url.rstrip('/')}/route/v1/driving/{origin.lon},{origin.lat};{dest.lon},{dest.lat}"
        try:
            async with httpx.AsyncClient(
                timeout=8, headers={"User-Agent": settings.user_agent}
            ) as c:
                resp = await c.get(url, params={"overview": "simplified", "geometries": "geojson"})
                resp.raise_for_status()
                coords = resp.json()["routes"][0]["geometry"]["coordinates"]
                return LineString(coords), "osrm"
        except Exception as exc:
            log.warning("osrm failed; straight line", extra={"err": str(exc)[:200]})
    return LineString([(origin.lon, origin.lat), (dest.lon, dest.lat)]), "straight_line"


def corridor(line: LineString, meters: float = CORRIDOR_M) -> BaseGeometry:
    return buffer_m(line, meters)


async def check_route(
    session: AsyncSession, origin: GeoMatch, dest: GeoMatch, depart: datetime, now: datetime
) -> dict[str, Any]:
    line, method = await route_line(origin, dest)
    area = corridor(line)
    wkt = area.wkt
    w_start, w_end = depart - timedelta(hours=1), depart + timedelta(hours=3)
    advisories = (
        await session.execute(
            text(
                """SELECT c.id, d.title, c.event_type, c.severity, c.valid_from, c.valid_to
                   FROM chunks c JOIN documents d ON d.id = c.document_id
                   WHERE c.status='ready' AND c.is_canonical AND c.geom IS NOT NULL
                     AND ST_Intersects(c.geom, ST_GeomFromText(:a,4326))
                     AND COALESCE(c.valid_from,'-infinity'::timestamptz) <= :e
                     AND COALESCE(c.valid_to,'infinity'::timestamptz) >= :s
                     AND (c.valid_from IS NOT NULL OR c.valid_to IS NOT NULL)
                   ORDER BY CASE c.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT 10"""
            ),
            {"a": wkt, "s": w_start, "e": w_end},
        )
    ).all()
    incidents = (
        await session.execute(
            text(
                """SELECT id, category, description, delay_seconds FROM traffic_incidents
                   WHERE is_active AND geom IS NOT NULL AND ST_Intersects(geom, ST_GeomFromText(:a,4326))
                   ORDER BY COALESCE(delay_seconds,0) DESC LIMIT 10"""
            ),
            {"a": wkt},
        )
    ).all()
    segs = (
        await session.execute(
            text(
                """SELECT DISTINCT ON (s.id) s.id, s.name, o.current_speed, o.free_flow_speed, o.observed_at
                   FROM segments s JOIN traffic_flow_obs o ON o.segment_id = s.id
                   WHERE ST_Intersects(ST_Buffer(s.geom::geography, 200)::geometry, ST_GeomFromText(:a,4326))
                     AND o.observed_at > :since
                   ORDER BY s.id, o.observed_at DESC"""
            ),
            {"a": wkt, "since": now - timedelta(hours=3)},
        )
    ).all()
    mid = line.interpolate(0.5, normalized=True)
    weather = (
        await session.execute(
            text(
                """SELECT flags, observed_at FROM weather_obs WHERE zone = :z
                   AND observed_at BETWEEN :s AND :e AND array_length(flags,1) > 0"""
            ),
            {"z": nearest_zone(mid.y, mid.x), "s": w_start, "e": w_end},
        )
    ).all()
    return {
        "available": True,
        "origin": origin.name,
        "destination": dest.name,
        "depart": fmt_ist(depart),
        "route_method": method,
        "distance_km": round(haversine_m(origin.lat, origin.lon, dest.lat, dest.lon) / 1000, 1),
        "corridor_geojson": to_geojson(line),
        "advisories": [
            {"chunk_id": r.id, "title": r.title, "event_type": r.event_type, "severity": r.severity}
            for r in advisories
        ],
        "incidents": [
            {
                "id": r.id,
                "category": r.category,
                "description": r.description,
                "delay_min": round(r.delay_seconds / 60) if r.delay_seconds else None,
            }
            for r in incidents
        ],
        "segments": [
            {
                "segment_id": r.id,
                "name": r.name,
                "level": congestion_level(
                    r.current_speed / r.free_flow_speed if r.free_flow_speed else None
                ),
                "current_speed_kmph": round(r.current_speed),
                "as_of": fmt_ist(r.observed_at),
            }
            for r in segs
        ],
        "weather_flags": sorted({f for r in weather for f in r.flags}),
    }
