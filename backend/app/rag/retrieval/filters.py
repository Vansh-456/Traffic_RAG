"""Hard filters: time-window overlap and optional spatial radius.

The time filter keeps chunks whose validity window overlaps the query window;
chunks with unknown validity fall back to ``published_at`` within the window
± 1 day. The spatial filter (``ST_DWithin`` on geography) is applied only if
it leaves at least ``k`` candidates; otherwise the radius is doubled once and
then dropped (proximity is still used in scoring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.geocoding import GeoMatch
from app.rag.timewindow import TimeWindow

TIME_SQL = """
(
  ((c.valid_from IS NOT NULL OR c.valid_to IS NOT NULL)
    AND COALESCE(c.valid_from, '-infinity'::timestamptz) <= :w_end
    AND COALESCE(c.valid_to, 'infinity'::timestamptz) >= :w_start)
  OR
  (c.valid_from IS NULL AND c.valid_to IS NULL
    AND c.published_at BETWEEN CAST(:w_start AS timestamptz) - interval '1 day' AND CAST(:w_end AS timestamptz) + interval '1 day')
)
"""
BASE_SQL = "c.status = 'ready' AND c.is_canonical"
SPATIAL_SQL = (
    "c.geom IS NOT NULL AND ST_DWithin(c.geom::geography, "
    "ST_GeomFromText(:q_geom, 4326)::geography, :radius)"
)


@dataclass
class FilterSpec:
    window: TimeWindow | None
    query_geom: BaseGeometry | None = None
    radius_m: float | None = None
    use_spatial: bool = False
    event_types: list[str] | None = None
    trace: dict[str, Any] = field(default_factory=dict)

    def where(self) -> str:
        parts = [BASE_SQL]
        if self.window is not None:
            parts.append(TIME_SQL)
        if self.use_spatial and self.query_geom is not None:
            parts.append(SPATIAL_SQL)
        if self.event_types:
            parts.append("c.event_type = ANY(:event_types)")
        return " AND ".join(parts)

    def params(self) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if self.window is not None:
            p["w_start"], p["w_end"] = self.window.start, self.window.end
        if self.query_geom is not None:
            p["q_geom"] = self.query_geom.wkt
            p["radius"] = self.radius_m or 1500
        if self.event_types:
            p["event_types"] = self.event_types
        return p


def query_geometry(locations: list[GeoMatch]) -> tuple[BaseGeometry | None, float]:
    if not locations:
        return None, 0.0
    geom = unary_union([m.geometry for m in locations])
    return geom, float(max(m.radius_m for m in locations))


async def count_matching(session: AsyncSession, spec: FilterSpec) -> int:
    row = await session.execute(text(f"SELECT count(*) FROM chunks c WHERE {spec.where()}"), spec.params())
    return int(row.scalar_one())


async def build_filters(
    session: AsyncSession,
    window: TimeWindow | None,
    locations: list[GeoMatch],
    k: int,
    use_geo: bool = True,
    event_types: list[str] | None = None,
) -> FilterSpec:
    """Choose the spatial radius with the relax-×2-then-drop policy."""
    geom, radius = query_geometry(locations)
    spec = FilterSpec(window=window, query_geom=geom, radius_m=radius, event_types=event_types)
    spec.trace = {"time_window": window.to_dict() if window else None, "spatial": "none"}
    if geom is None or not use_geo:
        spec.trace["spatial"] = "disabled" if geom is not None else "no_location"
        spec.trace["count"] = await count_matching(session, spec)
        return spec
    spec.use_spatial = True
    attempts = []
    for r in (radius, radius * 2):
        spec.radius_m = r
        n = await count_matching(session, spec)
        attempts.append({"radius_m": r, "count": n})
        if n >= k:
            spec.trace.update({"spatial": f"within {int(r)} m", "attempts": attempts, "count": n})
            return spec
    spec.use_spatial = False
    spec.radius_m = radius
    n = await count_matching(session, spec)
    attempts.append({"radius_m": None, "count": n})
    spec.trace.update({"spatial": "dropped (too few results); proximity used in scoring", "attempts": attempts, "count": n})
    return spec
