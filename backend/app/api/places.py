"""Place autocomplete over the gazetteer (names, Hindi names, aliases)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.ingestion.geocoding import get_geocoder, normalize_mention

router = APIRouter(prefix="/api", tags=["places"])


@router.get("/places/search")
async def places_search(q: str = Query(min_length=1, max_length=100), limit: int = Query(8, le=20), db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """Autocomplete across names, Hindi names and aliases (prefix + trigram)."""
    norm = normalize_mention(q)
    rows = (await db.execute(text(
        """SELECT id, name, name_hi, type, ST_Y(centroid) AS lat, ST_X(centroid) AS lon,
                  GREATEST(similarity(search_text, :q), word_similarity(:q, search_text),
                           CASE WHEN search_text LIKE :prefix OR search_text LIKE :inner THEN 0.9 ELSE 0 END) AS score
           FROM places
           WHERE search_text LIKE :prefix OR search_text LIKE :inner OR :q <% search_text
           ORDER BY score DESC, importance DESC LIMIT :limit"""
    ), {"q": norm, "prefix": f"{norm}%", "inner": f"%| {norm}%", "limit": limit})).mappings().all()
    if rows:
        return [dict(r) for r in rows]
    # DB empty (fresh install) → seed index
    out = []
    for _alias, rec in get_geocoder().index.find_mentions(q)[:limit]:
        out.append({"id": rec.place_id, "name": rec.name, "name_hi": rec.name_hi, "type": rec.type, "lat": rec.lat, "lon": rec.lon, "score": 1.0})
    return out
