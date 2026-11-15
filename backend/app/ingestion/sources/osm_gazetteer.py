"""Gazetteer loading: hand-curated seed + OpenStreetMap Overpass import.

Seed places (data/seed/aliases.yaml) carry curated Hindi names and aliases and
always win over OSM for the same name. OSM adds breadth: junctions, flyovers,
major roads (with geometry), metro stations, stadiums/venues, hospitals,
markets. Run monthly (scheduler) or via ``copilot ingest gazetteer``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.geo import DELHI_BBOX
from app.ingestion.geocoding import PlaceRecord, load_seed_places, normalize_mention, seed_path
from app.observability.logging import get_logger

log = get_logger(__name__)

_LON_MIN, _LAT_MIN, _LON_MAX, _LAT_MAX = DELHI_BBOX
_BBOX = f"{_LAT_MIN},{_LON_MIN},{_LAT_MAX},{_LON_MAX}"

OVERPASS_POI_QUERY = f"""
[out:json][timeout:180];
(
  node["highway"="motorway_junction"]["name"]({_BBOX});
  node["junction"]["name"]({_BBOX});
  way["bridge"]["highway"~"trunk|primary|motorway"]["name"~"[Ff]lyover"]({_BBOX});
  node["railway"="station"]["name"]({_BBOX});
  node["station"="subway"]["name"]({_BBOX});
  nwr["leisure"="stadium"]["name"]({_BBOX});
  nwr["amenity"="hospital"]["name"]({_BBOX});
  nwr["amenity"="marketplace"]["name"]({_BBOX});
  nwr["shop"="mall"]["name"]({_BBOX});
  nwr["tourism"="attraction"]["name"]({_BBOX});
  nwr["amenity"="events_venue"]["name"]({_BBOX});
);
out center tags;
"""

OVERPASS_ROADS_QUERY = f"""
[out:json][timeout:240];
way["highway"~"^(motorway|trunk|primary)$"]["name"]({_BBOX});
out geom tags;
"""


@dataclass
class OsmPlace:
    osm_id: str
    name: str
    name_hi: str | None
    type: str
    lat: float
    lon: float
    geom_wkt: str | None
    importance: float
    tags: dict[str, Any]


def classify(tags: dict[str, str]) -> tuple[str, float]:
    """Map OSM tags to our place type and an importance prior."""
    if tags.get("station") == "subway" or "metro" in tags.get("network", "").lower():
        return "metro", 0.6
    if tags.get("railway") == "station":
        return "station", 0.6
    if tags.get("leisure") == "stadium" or tags.get("amenity") == "events_venue":
        return "venue", 0.7
    if tags.get("amenity") == "hospital":
        return "hospital", 0.4
    if tags.get("amenity") == "marketplace" or tags.get("shop") == "mall":
        return "market", 0.4
    if tags.get("bridge"):
        return "bridge", 0.6
    if tags.get("highway") == "motorway_junction" or tags.get("junction"):
        return "junction", 0.6
    if tags.get("tourism") == "attraction":
        return "landmark", 0.5
    return "landmark", 0.3


def parse_pois(payload: dict[str, Any]) -> list[OsmPlace]:
    out: list[OsmPlace] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name:en") or tags.get("name")
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        ptype, importance = classify(tags)
        out.append(
            OsmPlace(
                osm_id=f"{el['type']}/{el['id']}",
                name=name,
                name_hi=tags.get("name:hi"),
                type=ptype,
                lat=float(lat),
                lon=float(lon),
                geom_wkt=None,
                importance=importance,
                tags={
                    k: v for k, v in tags.items() if k in ("highway", "amenity", "railway", "ref")
                },
            )
        )
    return out


def parse_roads(payload: dict[str, Any]) -> list[OsmPlace]:
    """Group named road ways into one MultiLineString per road name."""
    groups: dict[str, list[LineString]] = defaultdict(list)
    meta: dict[str, dict[str, str]] = {}
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name:en") or tags.get("name")
        coords = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        if not name or len(coords) < 2:
            continue
        groups[name].append(LineString(coords))
        meta.setdefault(name, tags)
    out: list[OsmPlace] = []
    for name, lines in groups.items():
        merged = linemerge(MultiLineString(lines))
        simplified = merged.simplify(0.0003, preserve_topology=False)
        if simplified.is_empty:
            continue
        c = (
            simplified.interpolate(0.5, normalized=True)
            if simplified.geom_type == "LineString"
            else simplified.centroid
        )
        tags = meta[name]
        importance = {"motorway": 0.9, "trunk": 0.8, "primary": 0.6}.get(
            tags.get("highway", ""), 0.5
        )
        out.append(
            OsmPlace(
                osm_id=f"road/{normalize_mention(name).replace(' ', '-')}",
                name=name,
                name_hi=tags.get("name:hi"),
                type="road",
                lat=c.y,
                lon=c.x,
                geom_wkt=simplified.wkt,
                importance=importance,
                tags={"ref": tags.get("ref", ""), "highway": tags.get("highway", "")},
            )
        )
    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=60))
async def _overpass(query: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=300, headers={"User-Agent": settings.user_agent}
    ) as client:
        resp = await client.post(settings.overpass_url, data={"data": query})
        resp.raise_for_status()
        return resp.json()


def _search_text(name: str, name_hi: str | None, aliases: list[str]) -> str:
    parts = [name, *(aliases or [])]
    if name_hi:
        parts.append(name_hi)
    return " | ".join(normalize_mention(p) for p in parts if p)


_UPSERT = text(
    """
    INSERT INTO places (osm_id, name, name_hi, aliases, search_text, type, importance,
                        geom, centroid, source, tags)
    VALUES (:osm_id, :name, :name_hi, :aliases, :search_text, :type, :importance,
            ST_GeomFromText(:wkt, 4326), ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
            :source, CAST(:tags AS jsonb))
    ON CONFLICT (osm_id) DO UPDATE SET
        name = EXCLUDED.name, name_hi = COALESCE(EXCLUDED.name_hi, places.name_hi),
        aliases = EXCLUDED.aliases, search_text = EXCLUDED.search_text, type = EXCLUDED.type,
        importance = EXCLUDED.importance, geom = EXCLUDED.geom, centroid = EXCLUDED.centroid,
        tags = EXCLUDED.tags
    """
)


async def upsert_seed(session: AsyncSession, places: list[PlaceRecord] | None = None) -> int:
    """Insert/update curated seed places. Returns count."""
    import json

    places = places if places is not None else load_seed_places(seed_path())
    for p in places:
        wkt = p.geom_wkt or f"POINT({p.lon} {p.lat})"
        await session.execute(
            _UPSERT,
            {
                "osm_id": p.key,
                "name": p.name,
                "name_hi": p.name_hi,
                "aliases": p.aliases,
                "search_text": _search_text(p.name, p.name_hi, p.aliases),
                "type": p.type,
                "importance": p.importance,
                "wkt": wkt,
                "lat": p.lat,
                "lon": p.lon,
                "source": "seed",
                "tags": json.dumps({}),
            },
        )
    return len(places)


async def import_osm(session: AsyncSession) -> dict[str, int]:
    """Fetch POIs and roads from Overpass and upsert (seed names are skipped)."""
    import json

    seed_names = {
        normalize_mention(n) for p in load_seed_places(seed_path()) for n in p.all_names()
    }
    pois = parse_pois(await _overpass(OVERPASS_POI_QUERY))
    roads = parse_roads(await _overpass(OVERPASS_ROADS_QUERY))
    counts = {"pois": 0, "roads": 0, "skipped_seed_dupes": 0}
    for p in [*pois, *roads]:
        if normalize_mention(p.name) in seed_names:
            counts["skipped_seed_dupes"] += 1
            continue
        await session.execute(
            _UPSERT,
            {
                "osm_id": p.osm_id,
                "name": p.name,
                "name_hi": p.name_hi,
                "aliases": [],
                "search_text": _search_text(p.name, p.name_hi, []),
                "type": p.type,
                "importance": p.importance,
                "wkt": p.geom_wkt or f"POINT({p.lon} {p.lat})",
                "lat": p.lat,
                "lon": p.lon,
                "source": "osm",
                "tags": json.dumps(p.tags),
            },
        )
        counts["roads" if p.type == "road" else "pois"] += 1
    log.info("osm gazetteer imported", extra=counts)
    return counts
