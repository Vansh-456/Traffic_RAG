"""Resolve place mentions (English / Hindi / Hinglish) to gazetteer places.

Resolution order (each step only if the previous failed):

1. **Alias lookup** - exact match of the normalised mention against every
   place name, Hindi name and alias (in-memory index, O(1)).
2. **Fuzzy match** - Postgres ``pg_trgm`` word similarity on
   ``places.search_text`` when a DB session is given; otherwise an in-memory
   trigram Jaccard over the alias index.
3. **Nominatim** (optional, ``NOMINATIM_ENABLED``) - bounded to the Delhi NCR
   bbox, 1 req/s, results cached in Redis for 30 days.

The same index powers ``find_mentions`` which spots known places in free text
(used by query understanding when the LLM is unavailable and to cross-check
LLM extractions).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.geo import DELHI_BBOX, in_delhi, line_from_latlon, point
from app.observability.logging import get_logger

log = get_logger(__name__)

DEFAULT_RADIUS_M = {
    "junction": 1500,
    "landmark": 1500,
    "venue": 1500,
    "station": 1200,
    "hospital": 1000,
    "market": 1200,
    "bridge": 1000,
    "area": 2500,
    "road_area": 1500,
    "road": 500,  # buffer around the road line
    "metro": 800,
}

_FILLER = re.compile(
    r"^(?:near|at|around|the|on|in|towards|from|to|ke paas|ke pass|ke paas ka|के पास|पास)\s+|\s+(?:ke paas|ke pass|के पास|पर|में|area|ilaka|इलाका)$",
    re.IGNORECASE,
)
# Hindi nukta / chandrabindu normalisation so ज़/ज, क़/क etc. match.
_NUKTA = str.maketrans({"़": None, "ँ": "ं"})


def normalize_mention(text_: str) -> str:
    s = unicodedata.normalize("NFC", text_).translate(_NUKTA).lower().strip()
    s = re.sub(r"[\"'`.,;:!?()\[\]{}]", " ", s)
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:
        prev = s
        s = _FILLER.sub("", s).strip()
    return s


def _trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i : i + 3] for i in range(len(s) - 2)}


@dataclass
class PlaceRecord:
    """Lightweight in-memory copy of a gazetteer place."""

    key: str
    name: str
    type: str
    lat: float
    lon: float
    name_hi: str | None = None
    aliases: list[str] = field(default_factory=list)
    geom_wkt: str | None = None
    place_id: int | None = None
    importance: float = 0.5

    @property
    def geometry(self) -> BaseGeometry:
        if self.geom_wkt:
            return shapely_wkt.loads(self.geom_wkt)
        return point(self.lat, self.lon)

    def all_names(self) -> list[str]:
        names = [self.name, *self.aliases]
        if self.name_hi:
            names.append(self.name_hi)
        return names


@dataclass
class GeoMatch:
    """Result of geocoding one mention."""

    mention: str
    name: str
    type: str
    lat: float
    lon: float
    confidence: float
    method: str  # alias|trigram|fuzzy|nominatim
    radius_m: int
    place_id: int | None = None
    geom_wkt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mention": self.mention,
            "name": self.name,
            "type": self.type,
            "lat": self.lat,
            "lon": self.lon,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "radius_m": self.radius_m,
            "place_id": self.place_id,
        }

    @property
    def geometry(self) -> BaseGeometry:
        if self.geom_wkt:
            return shapely_wkt.loads(self.geom_wkt)
        return point(self.lat, self.lon)


def load_seed_places(path: Path) -> list[PlaceRecord]:
    """Parse `data/seed/aliases.yaml` into PlaceRecords (roads get LineStrings)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[PlaceRecord] = []
    for item in data.get("places", []):
        geom_wkt = None
        if item.get("line"):
            line = line_from_latlon(item["line"])
            geom_wkt = line.wkt
            c = line.interpolate(0.5, normalized=True)
            lat, lon = c.y, c.x
        else:
            lat, lon = float(item["lat"]), float(item["lon"])
        out.append(
            PlaceRecord(
                key=f"seed:{normalize_mention(item['name']).replace(' ', '-')}",
                name=item["name"],
                type=item["type"],
                lat=lat,
                lon=lon,
                name_hi=item.get("name_hi"),
                aliases=[str(a) for a in item.get("aliases") or []],
                geom_wkt=geom_wkt,
                importance=1.0,
            )
        )
    return out


class AliasIndex:
    """Normalised-name → place index with longest-match mention finding."""

    def __init__(self, places: list[PlaceRecord]) -> None:
        self.places = places
        self.by_alias: dict[str, list[PlaceRecord]] = {}
        for p in places:
            for name in p.all_names():
                norm = normalize_mention(name)
                if len(norm) < 2:
                    continue
                bucket = self.by_alias.setdefault(norm, [])
                if p not in bucket:
                    bucket.append(p)
        for bucket in self.by_alias.values():
            bucket.sort(key=lambda r: (-r.importance, r.type == "road"))
        # aliases sorted longest-first for greedy mention spotting
        self._alias_patterns = sorted(self.by_alias, key=len, reverse=True)
        self._trigram_cache = {a: _trigrams(a) for a in self.by_alias}

    def lookup(self, mention: str) -> PlaceRecord | None:
        bucket = self.by_alias.get(normalize_mention(mention))
        return bucket[0] if bucket else None

    def fuzzy(self, mention: str, threshold: float = 0.5) -> tuple[PlaceRecord, float] | None:
        q = normalize_mention(mention)
        if len(q) < 3:
            return None
        q_tri = _trigrams(q)
        best: tuple[PlaceRecord, float] | None = None
        for alias, tri in self._trigram_cache.items():
            score = len(q_tri & tri) / len(q_tri | tri)
            if best is None or score > best[1]:
                best = (self.by_alias[alias][0], score)
        if best and best[1] >= threshold:
            return best
        return None

    def find_mentions(self, text_: str) -> list[tuple[str, PlaceRecord]]:
        """Greedy longest-match scan for known place names inside free text."""
        norm = normalize_mention(text_)
        padded = f" {norm} "
        found: list[tuple[str, PlaceRecord]] = []
        taken: list[tuple[int, int]] = []
        for alias in self._alias_patterns:
            if len(alias) < 2:
                continue
            # word-boundary match; Devanagari has no \b so use space padding
            for m in re.finditer(re.escape(f" {alias} "), padded):
                start, end = m.start() + 1, m.end() - 1
                if any(s < end and start < e for s, e in taken):
                    continue
                # Very short ASCII aliases (CP, DU) must be upper-case in the source
                if (
                    len(alias) <= 3
                    and alias.isascii()
                    and not re.search(rf"\b{re.escape(alias)}\b", text_, re.IGNORECASE)
                ):
                    continue
                taken.append((start, end))
                found.append((alias, self.by_alias[alias][0]))
        # keep order of appearance
        found.sort(key=lambda f: padded.find(f" {f[0]} "))
        unique: list[tuple[str, PlaceRecord]] = []
        seen: set[str] = set()
        for alias, rec in found:
            if rec.key not in seen:
                seen.add(rec.key)
                unique.append((alias, rec))
        return unique


def _match_from_record(mention: str, rec: PlaceRecord, conf: float, method: str) -> GeoMatch:
    return GeoMatch(
        mention=mention,
        name=rec.name,
        type=rec.type,
        lat=rec.lat,
        lon=rec.lon,
        confidence=conf,
        method=method,
        radius_m=DEFAULT_RADIUS_M.get(rec.type, 1500),
        place_id=rec.place_id,
        geom_wkt=rec.geom_wkt,
    )


class Geocoder:
    """Multi-stage geocoder; safe to share across tasks."""

    def __init__(self, index: AliasIndex, redis: Any | None = None) -> None:
        self.index = index
        self.redis = redis
        self.settings = get_settings()
        self._nominatim_lock = asyncio.Lock()
        self._last_nominatim = 0.0

    async def geocode(self, mention: str, session: AsyncSession | None = None) -> GeoMatch | None:
        mention = mention.strip()
        if not mention or len(mention) > 200:
            return None
        rec = self.index.lookup(mention)
        if rec:
            return _match_from_record(mention, rec, 1.0, "alias")
        # substring containment, e.g. "ITO flyover towards Vikas Marg"
        spotted = self.index.find_mentions(mention)
        if spotted:
            return _match_from_record(mention, spotted[0][1], 0.85, "alias")
        if session is not None:
            match = await self._trigram_db(mention, session)
            if match:
                return match
        fuzzy = self.index.fuzzy(mention)
        if fuzzy:
            return _match_from_record(mention, fuzzy[0], fuzzy[1], "fuzzy")
        if self.settings.nominatim_enabled:
            return await self._nominatim(mention)
        return None

    async def geocode_many(
        self, mentions: list[str], session: AsyncSession | None = None
    ) -> list[GeoMatch]:
        out: list[GeoMatch] = []
        seen: set[str] = set()
        for m in mentions:
            match = await self.geocode(m, session)
            if match and match.name not in seen:
                seen.add(match.name)
                out.append(match)
        return out

    async def _trigram_db(self, mention: str, session: AsyncSession) -> GeoMatch | None:
        q = normalize_mention(mention)
        if len(q) < 3:
            return None
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, name, type, ST_Y(centroid) AS lat, ST_X(centroid) AS lon,
                           ST_AsText(geom) AS wkt, word_similarity(:q, search_text) AS sim
                    FROM places
                    WHERE :q <% search_text
                    ORDER BY sim DESC, importance DESC
                    LIMIT 1
                    """
                ),
                {"q": q},
            )
        ).first()
        if row is None or row.sim < 0.55:
            return None
        return GeoMatch(
            mention=mention,
            name=row.name,
            type=row.type,
            lat=row.lat,
            lon=row.lon,
            confidence=float(row.sim) * 0.9,
            method="trigram",
            radius_m=DEFAULT_RADIUS_M.get(row.type, 1500),
            place_id=row.id,
            geom_wkt=row.wkt if row.type == "road" else None,
        )

    async def _nominatim(self, mention: str) -> GeoMatch | None:
        cache_key = f"geocode:nominatim:{normalize_mention(mention)}"
        if self.redis is not None:
            try:
                cached = await self.redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return GeoMatch(**data) if data else None
            except Exception:
                pass
        async with self._nominatim_lock:  # Nominatim policy: max 1 req/s
            wait = 1.1 - (time.monotonic() - self._last_nominatim)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_nominatim = time.monotonic()
            lon_min, lat_min, lon_max, lat_max = DELHI_BBOX
            try:
                async with httpx.AsyncClient(
                    timeout=10, headers={"User-Agent": self.settings.user_agent}
                ) as client:
                    resp = await client.get(
                        f"{self.settings.nominatim_url}/search",
                        params={
                            "q": f"{mention}, Delhi",
                            "format": "jsonv2",
                            "limit": 1,
                            "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}",
                            "bounded": 1,
                        },
                    )
                    resp.raise_for_status()
                    results = resp.json()
            except Exception as exc:
                log.warning("nominatim failed", extra={"err": str(exc)[:200]})
                return None
        match = None
        if results:
            r = results[0]
            lat, lon = float(r["lat"]), float(r["lon"])
            if in_delhi(lat, lon):
                match = GeoMatch(
                    mention=mention,
                    name=r.get("name") or r.get("display_name", mention).split(",")[0],
                    type="area",
                    lat=lat,
                    lon=lon,
                    confidence=min(0.8, float(r.get("importance", 0.5)) + 0.3),
                    method="nominatim",
                    radius_m=1500,
                )
        if self.redis is not None:
            try:
                payload = json.dumps(match.__dict__ if match else None)
                await self.redis.set(cache_key, payload, ex=30 * 86400)
            except Exception:
                pass
        return match


_geocoder: Geocoder | None = None


def seed_path() -> Path:
    """Locate data/seed/aliases.yaml from repo root or container (/app/data)."""
    here = Path(__file__).resolve()
    for base in [*here.parents, Path.cwd()]:
        candidate = base / "data" / "seed" / "aliases.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("data/seed/aliases.yaml not found")


async def build_index_from_db(session: AsyncSession) -> AliasIndex:
    """Index all places in the DB (seed + OSM)."""
    rows = (
        await session.execute(
            text(
                """SELECT id, osm_id, name, name_hi, aliases, type, importance,
                          ST_Y(centroid) AS lat, ST_X(centroid) AS lon,
                          CASE WHEN type = 'road' THEN ST_AsText(geom) END AS wkt
                   FROM places"""
            )
        )
    ).all()
    records = [
        PlaceRecord(
            key=r.osm_id or f"id:{r.id}",
            name=r.name,
            type=r.type,
            lat=r.lat,
            lon=r.lon,
            name_hi=r.name_hi,
            aliases=list(r.aliases or []),
            geom_wkt=r.wkt,
            place_id=r.id,
            importance=r.importance,
        )
        for r in rows
    ]
    return AliasIndex(records)


def get_geocoder() -> Geocoder:
    """Seed-backed geocoder; call `refresh_geocoder` to load DB places."""
    global _geocoder
    if _geocoder is None:
        _geocoder = Geocoder(AliasIndex(load_seed_places(seed_path())))
    return _geocoder


async def get_db_geocoder(session: AsyncSession) -> Geocoder:
    """Geocoder whose places carry DB ids (needed for chunk_locations.place_id).

    Falls back to the seed index when the places table is still empty.
    """
    current = get_geocoder()
    if any(p.place_id for p in current.index.places):
        return current
    return await refresh_geocoder(session, current.redis)


async def refresh_geocoder(session: AsyncSession, redis: Any | None = None) -> Geocoder:
    global _geocoder
    index = await build_index_from_db(session)
    if not index.places:
        index = AliasIndex(load_seed_places(seed_path()))
    _geocoder = Geocoder(index, redis)
    return _geocoder
