"""Public read endpoints: advisories feed, live map, place search, feedback."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limit
from app.db.models.users import Feedback, Message
from app.db.session import get_db
from app.timeutil import utcnow

router = APIRouter(prefix="/api", tags=["public"])

EVENT_TYPES = {
    "vip_movement", "protest", "festival", "procession", "sports_event", "roadwork",
    "construction", "waterlogging", "accident", "breakdown", "closure", "general_advisory", "other",
}


@router.get("/advisories")
async def advisories(
    active: bool = True,
    near: str | None = Query(None, description="lat,lon"),
    radius: float = Query(3000, ge=100, le=50000),
    type: str | None = None,
    source: str | None = Query(None, pattern="^(official|news|sensor)$"),
    upcoming: bool = False,
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Live/upcoming advisory feed of canonical chunks (one row per document)."""
    where = ["c.status = 'ready'", "c.is_canonical", "c.ordinal = 0"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size, "now": utcnow()}
    if active and not upcoming:
        where.append("c.is_active AND (c.valid_from IS NULL OR c.valid_from <= CAST(:now AS timestamptz) + interval '1 day')")
        where.append("(c.valid_to IS NULL OR c.valid_to >= :now) AND COALESCE(c.published_at, c.created_at) > CAST(:now AS timestamptz) - interval '21 days'")
    if upcoming:
        where.append("c.valid_from > :now")
    if type:
        if type not in EVENT_TYPES:
            raise HTTPException(400, "unknown type")
        where.append("c.event_type = :type")
        params["type"] = type
    if source:
        where.append("c.trust_level = :src")
        params["src"] = source
    if near:
        try:
            lat, lon = (float(x) for x in near.split(","))
        except ValueError as exc:
            raise HTTPException(400, "near must be 'lat,lon'") from exc
        where.append("c.geom IS NOT NULL AND ST_DWithin(c.geom::geography, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography, :radius)")
        params.update(lat=lat, lon=lon, radius=radius)
    sql = f"""
        SELECT c.id AS chunk_id, d.id AS document_id, d.title, d.url, d.source_id, d.authority, d.publisher,
               d.language, d.linked_document_id, c.trust_level, c.event_type, c.severity, c.valid_from,
               c.valid_to, c.published_at, c.metadata->>'summary_en' AS summary_en,
               c.metadata->>'summary_hi' AS summary_hi, left(c.text, 600) AS snippet,
               CASE WHEN c.geom IS NOT NULL THEN ST_AsGeoJSON(c.geom)::json END AS geometry,
               (SELECT count(*) - 1 FROM chunks c2 WHERE c2.dedup_cluster_id = c.dedup_cluster_id) AS also_reported_by,
               count(*) OVER () AS total
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE c.trust_level WHEN 'official' THEN 0 WHEN 'sensor' THEN 1 ELSE 2 END,
                 COALESCE(c.valid_from, c.published_at) DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """
    rows = (await db.execute(text(sql), params)).mappings().all()
    items = [{k: v for k, v in r.items() if k != "total"} for r in rows]
    return {"page": page, "page_size": page_size, "total": int(rows[0]["total"]) if rows else 0, "items": items}


@router.get("/advisories/{document_id}")
async def advisory_detail(document_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    doc = (await db.execute(text(
        "SELECT id, title, url, authority, publisher, language, published_at, trust_level, linked_document_id, text FROM documents WHERE id = :id"
    ), {"id": document_id})).mappings().first()
    if doc is None:
        raise HTTPException(404, "not found")
    chunks = (await db.execute(text(
        """SELECT id, ordinal, section, text, event_type, severity, valid_from, valid_to, metadata,
                  CASE WHEN geom IS NOT NULL THEN ST_AsGeoJSON(geom)::json END AS geometry
           FROM chunks WHERE document_id = :id ORDER BY ordinal"""
    ), {"id": document_id})).mappings().all()
    return {**dict(doc), "chunks": [dict(c) for c in chunks]}


@router.get("/map/live")
async def map_live(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """GeoJSON layers: monitored segment congestion, active incidents, active advisory areas."""
    now = utcnow()
    segs = (await db.execute(text(
        """SELECT DISTINCT ON (s.id) s.id, s.name, s.road, ST_AsGeoJSON(s.geom)::json AS g,
                  o.current_speed, o.free_flow_speed, o.road_closure, o.observed_at
           FROM segments s LEFT JOIN traffic_flow_obs o ON o.segment_id = s.id AND o.observed_at > :since
           ORDER BY s.id, o.observed_at DESC NULLS LAST"""
    ), {"since": now - timedelta(hours=2)})).all()
    incidents = (await db.execute(text(
        """SELECT id, category, description, delay_seconds, last_seen_at, ST_AsGeoJSON(geom)::json AS g
           FROM traffic_incidents WHERE is_active AND geom IS NOT NULL LIMIT 300"""
    ))).all()
    adv = (await db.execute(text(
        """SELECT c.id, d.title, d.url, c.event_type, c.severity, c.valid_to, ST_AsGeoJSON(c.geom)::json AS g
           FROM chunks c JOIN documents d ON d.id = c.document_id
           WHERE c.is_active AND c.is_canonical AND c.geom IS NOT NULL AND c.trust_level <> 'sensor'
             AND (c.valid_from IS NULL OR c.valid_from <= CAST(:now AS timestamptz) + interval '6 hours')
             AND (c.valid_to IS NULL OR c.valid_to >= :now)
             AND COALESCE(c.published_at, c.created_at) > CAST(:now AS timestamptz) - interval '14 days'
           LIMIT 300"""
    ), {"now": now})).all()

    def ratio(r: Any) -> float | None:
        return round(r.current_speed / r.free_flow_speed, 2) if r.current_speed and r.free_flow_speed else None

    from app.rag.tools.live import congestion_level

    return {
        "as_of": now.isoformat(),
        "segments": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": r.g, "properties": {
                "id": r.id, "name": r.name, "road": r.road, "ratio": ratio(r),
                "level": "closed" if r.road_closure else congestion_level(ratio(r)),
                "speed": round(r.current_speed) if r.current_speed else None,
                "free_flow": round(r.free_flow_speed) if r.free_flow_speed else None,
                "observed_at": r.observed_at.isoformat() if r.observed_at else None}}
            for r in segs if r.g]},
        "incidents": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": r.g, "properties": {
                "id": r.id, "category": r.category, "description": r.description,
                "delay_min": round(r.delay_seconds / 60) if r.delay_seconds else None,
                "last_seen_at": r.last_seen_at.isoformat()}} for r in incidents]},
        "advisories": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": r.g, "properties": {
                "chunk_id": r.id, "title": r.title, "url": r.url, "event_type": r.event_type,
                "severity": r.severity, "valid_to": r.valid_to.isoformat() if r.valid_to else None}} for r in adv]},
    }


class FeedbackIn(BaseModel):
    message_id: uuid.UUID
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(None, max_length=2000)


@router.post("/feedback", dependencies=[Depends(rate_limit)])
async def feedback(body: FeedbackIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if body.rating == 0:
        raise HTTPException(400, "rating must be +1 or -1")
    msg = await db.get(Message, body.message_id)
    if msg is None:
        raise HTTPException(404, "message not found")
    fb = Feedback(message_id=body.message_id, rating=body.rating, comment=body.comment, in_review_queue=body.rating < 0)
    db.add(fb)
    await db.flush()
    return {"id": fb.id, "in_review_queue": fb.in_review_queue}
