"""Historical queries via fixed, parameterised SQL templates (no free-form SQL)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.geocoding import GeoMatch
from app.rag.retrieval.filters import query_geometry
from app.timeutil import fmt_ist

ALLOWED_EVENT_TYPES = {
    "vip_movement",
    "protest",
    "festival",
    "procession",
    "sports_event",
    "roadwork",
    "construction",
    "waterlogging",
    "accident",
    "breakdown",
    "closure",
    "general_advisory",
    "other",
}


async def query_incident_history(
    session: AsyncSession,
    locations: list[GeoMatch],
    start: datetime,
    end: datetime,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Counts by event type (advisories/news) + TomTom incidents in a window/area."""
    if event_type and event_type not in ALLOWED_EVENT_TYPES:
        event_type = None
    geom, radius = query_geometry(locations)
    spatial = ""
    params: dict[str, Any] = {"s": start, "e": end, "et": event_type}
    if geom is not None:
        spatial = " AND c.geom IS NOT NULL AND ST_DWithin(c.geom::geography, ST_GeomFromText(:g,4326)::geography, :r)"
        params.update(g=geom.wkt, r=max(radius, 1500))
    by_type = (
        await session.execute(
            text(
                f"""SELECT c.event_type, count(DISTINCT COALESCE(c.dedup_cluster_id, c.id)) AS n
                    FROM chunks c
                    WHERE c.status = 'ready' AND COALESCE(c.valid_from, c.published_at) BETWEEN :s AND :e
                      AND (CAST(:et AS text) IS NULL OR c.event_type = :et) {spatial}
                    GROUP BY c.event_type ORDER BY n DESC"""
            ),
            params,
        )
    ).all()
    examples = (
        await session.execute(
            text(
                f"""SELECT c.id, d.title, c.event_type, COALESCE(c.valid_from, c.published_at) AS at
                    FROM chunks c JOIN documents d ON d.id = c.document_id
                    WHERE c.status = 'ready' AND c.is_canonical
                      AND COALESCE(c.valid_from, c.published_at) BETWEEN :s AND :e
                      AND (CAST(:et AS text) IS NULL OR c.event_type = :et) {spatial}
                    ORDER BY at DESC LIMIT 8"""
            ),
            params,
        )
    ).all()
    inc_spatial = spatial.replace("c.geom", "i.geom")
    incidents = (
        await session.execute(
            text(
                f"""SELECT i.category, count(*) AS n FROM traffic_incidents i
                    WHERE i.first_seen_at BETWEEN :s AND :e {inc_spatial}
                    GROUP BY i.category ORDER BY n DESC"""
            ),
            {k: v for k, v in params.items() if k != "et"},
        )
    ).all()
    return {
        "available": True,
        "window": {"start": fmt_ist(start), "end": fmt_ist(end)},
        "advisory_and_news_counts": {r.event_type or "other": int(r.n) for r in by_type},
        "tomtom_incident_counts": {r.category: int(r.n) for r in incidents},
        "examples": [
            {
                "chunk_id": r.id,
                "title": r.title,
                "event_type": r.event_type,
                "at": fmt_ist(r.at) if r.at else None,
            }
            for r in examples
        ],
    }
