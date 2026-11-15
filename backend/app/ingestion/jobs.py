"""Ingestion jobs: source registry, run bookkeeping and every scheduled job.

Each job runs under a Redis lock (no overlap), records an ``ingestion_runs``
row, updates ``sources.last_*`` health fields, and never raises into the
scheduler - failures are logged and counted.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Source
from app.db.models.geo import Event, Segment
from app.db.models.live import TrafficFlowObs, TrafficIncident, WeatherObs
from app.db.models.ops import IngestionRun
from app.db.session import session_scope
from app.ingestion import pipeline
from app.ingestion.geocoding import get_geocoder, refresh_geocoder, seed_path
from app.ingestion.http import PoliteFetcher
from app.ingestion.sources import dtp_advisories, google_news, osm_gazetteer, tomtom, weather
from app.observability.logging import get_logger
from app.observability.metrics import INGEST_ERRORS
from app.redis_client import CHANNEL_NEW_INCIDENT, LockNotAcquiredError, get_redis, job_lock
from app.timeutil import IST, now_ist, utcnow

log = get_logger(__name__)

SOURCES: list[dict[str, str]] = [
    {
        "id": "dtp_advisories",
        "name": "Delhi Traffic Police advisories",
        "kind": "advisory",
        "trust_level": "official",
    },
    {"id": "google_news", "name": "Google News RSS", "kind": "news", "trust_level": "news"},
    {"id": "extra_rss", "name": "Publisher RSS feeds", "kind": "news", "trust_level": "news"},
    {"id": "gdelt", "name": "GDELT news backfill", "kind": "news", "trust_level": "news"},
    {"id": "tomtom", "name": "TomTom flow + incidents", "kind": "sensor", "trust_level": "sensor"},
    {"id": "weather", "name": "Open-Meteo weather", "kind": "weather", "trust_level": "sensor"},
    {
        "id": "gazetteer",
        "name": "OSM gazetteer + seed aliases",
        "kind": "gazetteer",
        "trust_level": "official",
    },
    {"id": "system", "name": "Maintenance jobs", "kind": "system", "trust_level": "official"},
]


@dataclass
class JobStats:
    seen: int = 0
    new: int = 0
    errors: int = 0
    details: dict[str, Any] = field(default_factory=dict)


async def ensure_sources(session: AsyncSession) -> None:
    settings = get_settings()
    schedules = {
        "dtp_advisories": settings.schedule_dtp,
        "google_news": settings.schedule_news,
        "extra_rss": settings.schedule_rss,
        "tomtom": settings.schedule_tomtom_tick,
        "weather": settings.schedule_weather,
        "gazetteer": settings.schedule_gazetteer,
    }
    for s in SOURCES:
        stmt = insert(Source).values(**s, schedule=schedules.get(s["id"]), enabled=True)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Source.id],
            set_={"name": stmt.excluded.name, "schedule": stmt.excluded.schedule},
        )
        await session.execute(stmt)


async def run_job(
    source_id: str, job: str, fn: Callable[[JobStats], Awaitable[None]], lock_ttl: int = 3600
) -> JobStats:
    """Lock + bookkeeping wrapper around a job body."""
    stats = JobStats()
    try:
        async with job_lock(f"{source_id}:{job}", ttl_seconds=lock_ttl):
            async with session_scope() as s:
                src = await s.get(Source, source_id)
                if src is not None and not src.enabled:
                    log.info("source disabled; skipping", extra={"source": source_id})
                    return stats
                run = IngestionRun(
                    source_id=source_id, job=job, started_at=utcnow(), status="running"
                )
                s.add(run)
                await s.flush()
                run_id = run.id
            status, error = "ok", None
            try:
                await fn(stats)
            except Exception as exc:
                status, error = "error", f"{type(exc).__name__}: {exc}"
                stats.errors += 1
                INGEST_ERRORS.labels(source_id).inc()
                log.error(
                    "job failed",
                    extra={"source": source_id, "job": job, "tb": traceback.format_exc()[-2000:]},
                )
            if stats.errors and status == "ok":
                status = "partial"
            async with session_scope() as s:
                await s.execute(
                    update(IngestionRun)
                    .where(IngestionRun.id == run_id)
                    .values(
                        finished_at=utcnow(),
                        status=status,
                        items_seen=stats.seen,
                        items_new=stats.new,
                        errors=stats.errors,
                        error_message=error,
                        details=stats.details,
                    )
                )
                await s.execute(
                    update(Source)
                    .where(Source.id == source_id)
                    .values(last_run_at=utcnow(), last_status=status, last_error=error)
                )
    except LockNotAcquiredError:
        log.info("job already running; skipped", extra={"source": source_id, "job": job})
    log.info(
        "job finished",
        extra={
            "source": source_id,
            "job": job,
            "seen": stats.seen,
            "new": stats.new,
            "errors": stats.errors,
        },
    )
    return stats


async def _process_drafts(drafts: list[pipeline.DocumentDraft], stats: JobStats) -> list[int]:
    chunk_ids: list[int] = []
    for d in drafts:
        stats.seen += 1
        try:
            async with session_scope() as s:
                res = await pipeline.process(s, d)
            if res.status in ("new", "updated"):
                stats.new += 1
                chunk_ids += res.chunk_ids
        except Exception as exc:
            stats.errors += 1
            log.warning(
                "document failed", extra={"url": d.url, "err": f"{type(exc).__name__}: {exc}"[:300]}
            )
    return chunk_ids


async def _known_urls(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    async with session_scope() as s:
        rows = await s.execute(text("SELECT url FROM documents WHERE url = ANY(:u)"), {"u": urls})
        return {r[0] for r in rows}


# ---------------------------------------------------------------- DTP
async def job_dtp_incremental(stats: JobStats) -> None:
    async with PoliteFetcher() as f:
        urls = await dtp_advisories.discover_incremental(f)
        stats.details["discovered"] = len(urls)
        # fetch new ones + re-check the 10 most recent (content may be edited)
        known = await _known_urls(urls)
        todo = [u for u in urls if u not in known] + [u for u in urls if u in known][:10]
        for url in todo:
            try:
                drafts = await dtp_advisories.fetch_node(f, url)
            except Exception as exc:
                stats.errors += 1
                log.warning("dtp node failed", extra={"url": url, "err": str(exc)[:200]})
                continue
            await _process_drafts(drafts, stats)


async def job_dtp_backfill(
    stats: JobStats, start: int = 1, limit: int | None = None, max_id: int | None = None
) -> None:
    async with PoliteFetcher() as f:
        urls = await dtp_advisories.discover_backfill(f, start=start, max_id=max_id)
        if limit:
            urls = urls[:limit]
        stats.details["candidates"] = len(urls)
        relevant = 0
        for url in urls:
            try:
                drafts = await dtp_advisories.fetch_node(f, url)
            except Exception as exc:
                stats.errors += 1
                log.warning("dtp node failed", extra={"url": url, "err": str(exc)[:200]})
                continue
            if drafts:
                relevant += 1
                await _process_drafts(drafts, stats)
        stats.details["relevant_nodes"] = relevant


# ---------------------------------------------------------------- News
async def job_google_news(stats: JobStats) -> None:
    async with PoliteFetcher() as f:
        drafts = await google_news.collect_google_news(f)
    known = await _known_urls([d.url for d in drafts])
    await _process_drafts([d for d in drafts if d.url not in known], stats)


async def job_extra_rss(stats: JobStats) -> None:
    if not get_settings().extra_rss_feeds:
        stats.details["skipped"] = "EXTRA_RSS_FEEDS empty"
        return
    async with PoliteFetcher() as f:
        drafts = await google_news.collect_extra_rss(f)
    known = await _known_urls([d.url for d in drafts])
    await _process_drafts([d for d in drafts if d.url not in known], stats)


async def job_gdelt_backfill(stats: JobStats, days: int = 180) -> None:
    async with PoliteFetcher() as f:
        drafts = await google_news.collect_gdelt(f, days=days)
    known = await _known_urls([d.url for d in drafts])
    await _process_drafts([d for d in drafts if d.url not in known], stats)


# ---------------------------------------------------------------- TomTom
async def job_tomtom_tick(stats: JobStats) -> None:
    """Called every 5 min; the budget manager decides whether to actually poll."""
    settings = get_settings()
    if not settings.tomtom_enabled:
        stats.details["skipped"] = "TOMTOM_API_KEY missing"
        return
    budget = tomtom.BudgetManager(get_redis())
    async with session_scope() as s:
        segments = (await s.execute(select(Segment))).scalars().all()
    flow_cost = len(segments)
    now = now_ist()
    async with PoliteFetcher(delay=0.2) as f:
        if await budget.should_poll("incidents", 1, now) and await budget.try_spend(1, now):
            await budget.mark_polled("incidents", now)
            await _poll_incidents(f, settings.tomtom_api_key, stats)
        if flow_cost and await budget.should_poll("flow", flow_cost + 1, now):
            if await budget.try_spend(flow_cost, now):
                await budget.mark_polled("flow", now)
                await _poll_flow(f, segments, settings.tomtom_api_key, stats)
            else:
                stats.details["flow"] = "budget exhausted"
    stats.details["budget"] = await budget.status()


async def _poll_flow(f: PoliteFetcher, segments: Any, key: str, stats: JobStats) -> None:
    now = utcnow()
    observed = 0
    for seg in segments:
        try:
            obs = await tomtom.fetch_flow(
                f, tomtom.SegmentSeed(seg.id, seg.name, seg.road, seg.lat, seg.lon), key
            )
        except Exception as exc:
            stats.errors += 1
            log.warning("flow fetch failed", extra={"segment": seg.id, "err": str(exc)[:200]})
            continue
        if obs is None:
            continue
        observed += 1
        async with session_scope() as s:
            s.add(
                TrafficFlowObs(
                    segment_id=obs.segment_id,
                    observed_at=now,
                    current_speed=obs.current_speed,
                    free_flow_speed=obs.free_flow_speed,
                    current_travel_time=obs.current_travel_time,
                    free_flow_travel_time=obs.free_flow_travel_time,
                    confidence=obs.confidence,
                    road_closure=obs.road_closure,
                )
            )
            if len(obs.coordinates) >= 2:
                from shapely.geometry import LineString

                await s.execute(
                    text(
                        "UPDATE segments SET geom = ST_GeomFromText(:wkt, 4326) WHERE id = :id AND (geom IS NULL OR ST_GeometryType(geom) = 'ST_Point')"
                    ),
                    {"wkt": LineString(obs.coordinates).wkt, "id": obs.segment_id},
                )
    stats.seen += observed
    stats.details["flow_observations"] = observed
    await get_redis().publish(
        "events:flow_poll", json.dumps({"at": now.isoformat(), "n": observed})
    )


async def _poll_incidents(f: PoliteFetcher, key: str, stats: JobStats) -> None:
    incidents = await tomtom.fetch_incidents(f, key)
    now = utcnow()
    seen_ids = [i.id for i in incidents]
    stats.details["incidents"] = len(incidents)
    for inc in incidents:
        wkt = tomtom.incident_geometry_wkt(inc.geometry)
        async with session_scope() as s:
            existing = await s.get(TrafficIncident, inc.id)
            is_new = existing is None
            values = dict(
                category=inc.category,
                icon_category=inc.icon_category,
                description=inc.description,
                from_name=inc.from_name,
                to_name=inc.to_name,
                road_numbers=inc.road_numbers,
                delay_seconds=inc.delay_seconds,
                magnitude=inc.magnitude,
                length_m=inc.length_m,
                start_time=inc.start_time,
                end_time=inc.end_time,
                last_seen_at=now,
                is_active=True,
                raw=inc.raw,
            )
            if is_new:
                s.add(TrafficIncident(id=inc.id, first_seen_at=now, **values))
            else:
                for k, v in values.items():
                    setattr(existing, k, v)
            await s.flush()
            if wkt:
                await s.execute(
                    text(
                        "UPDATE traffic_incidents SET geom = ST_GeomFromText(:wkt, 4326) WHERE id = :id"
                    ),
                    {"wkt": wkt, "id": inc.id},
                )
            res = await pipeline.process(s, tomtom.incident_draft(inc))
            if res.document_id:
                await s.execute(
                    text("UPDATE traffic_incidents SET document_id = :d WHERE id = :id"),
                    {"d": res.document_id, "id": inc.id},
                )
                if wkt and res.chunk_ids:
                    await s.execute(
                        text(
                            "UPDATE chunks SET geom = ST_GeomFromText(:wkt, 4326) WHERE id = ANY(:ids)"
                        ),
                        {"wkt": wkt, "ids": res.chunk_ids},
                    )
        stats.seen += 1
        if is_new:
            stats.new += 1
            await get_redis().publish(CHANNEL_NEW_INCIDENT, json.dumps({"id": inc.id}))
    # incidents no longer reported → inactive, and their chunks expire now
    async with session_scope() as s:
        await s.execute(
            text(
                """
                WITH gone AS (
                  UPDATE traffic_incidents SET is_active = false, end_time = COALESCE(end_time, :now)
                  WHERE is_active AND NOT (id = ANY(:ids)) RETURNING document_id
                )
                UPDATE chunks SET is_active = false, valid_to = LEAST(COALESCE(valid_to, :now), :now)
                WHERE document_id IN (SELECT document_id FROM gone)
                """
            ),
            {"now": now, "ids": seen_ids},
        )


# ---------------------------------------------------------------- Weather
async def job_weather(stats: JobStats) -> None:
    async with PoliteFetcher(delay=0.5) as f:
        rows = await weather.fetch_weather(f, now_ist())
    async with session_scope() as s:
        for r in rows:
            stmt = insert(WeatherObs).values(
                zone=r.zone,
                lat=r.lat,
                lon=r.lon,
                observed_at=r.observed_at,
                kind=r.kind,
                temperature=r.temperature,
                precipitation=r.precipitation,
                rain=r.rain,
                visibility=r.visibility,
                weather_code=r.weather_code,
                flags=r.flags,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_weather_obs_zone_time_kind",
                set_={
                    c: stmt.excluded[c]
                    for c in (
                        "temperature",
                        "precipitation",
                        "rain",
                        "visibility",
                        "weather_code",
                        "flags",
                    )
                },
            )
            await s.execute(stmt)
        # an hour that was 'forecast' and is now past becomes 'history' on next fetch
        await s.execute(
            text(
                "DELETE FROM weather_obs WHERE kind='forecast' AND observed_at < now() - interval '2 hours'"
            )
        )
    stats.seen = len(rows)
    stats.details["flags"] = sorted({fl for r in rows if r.kind == "current" for fl in r.flags})


# ---------------------------------------------------------------- Gazetteer / seed
async def job_gazetteer(stats: JobStats, with_osm: bool = True) -> None:
    async with session_scope() as s:
        stats.new += await osm_gazetteer.upsert_seed(s)
    if with_osm:
        try:
            async with session_scope() as s:
                counts = await osm_gazetteer.import_osm(s)
            stats.details.update(counts)
            stats.new += counts["pois"] + counts["roads"]
        except Exception as exc:
            stats.errors += 1
            stats.details["osm_error"] = str(exc)[:300]
    async with session_scope() as s:
        await refresh_geocoder(s, get_redis())


async def seed_segments_and_events() -> dict[str, int]:
    base = seed_path().parent
    segs = tomtom.load_segments(base / "segments.yaml")
    events = yaml.safe_load(Path(base / "events.yaml").read_text(encoding="utf-8"))["events"]
    geocoder = get_geocoder()
    async with session_scope() as s:
        for seg in segs:
            stmt = insert(Segment).values(
                id=seg.id,
                name=seg.name,
                road=seg.road,
                lat=seg.lat,
                lon=seg.lon,
                geom=f"SRID=4326;POINT({seg.lon} {seg.lat})",
                aliases=[seg.road],
            )
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=[Segment.id],
                    set_={
                        "name": stmt.excluded.name,
                        "road": stmt.excluded.road,
                        "lat": stmt.excluded.lat,
                        "lon": stmt.excluded.lon,
                    },
                )
            )
        await s.execute(text("DELETE FROM events WHERE source = 'seed'"))
        for e in events:
            match = await geocoder.geocode(e["venue"], s)
            s.add(
                Event(
                    name=e["name"],
                    venue=e["venue"],
                    place_id=match.place_id if match else None,
                    start_at=datetime.fromisoformat(e["start"]).replace(tzinfo=IST),
                    end_at=datetime.fromisoformat(e["end"]).replace(tzinfo=IST),
                    source="seed",
                )
            )
    return {"segments": len(segs), "events": len(events)}


# ---------------------------------------------------------------- Maintenance
async def job_expire(stats: JobStats) -> None:
    async with session_scope() as s:
        res = await s.execute(
            text("UPDATE chunks SET is_active = false WHERE is_active AND valid_to < now()")
        )
        stats.new = res.rowcount or 0  # type: ignore[attr-defined]


async def job_retention(stats: JobStats) -> None:
    settings = get_settings()
    cutoff = utcnow() - timedelta(days=settings.raw_retention_days)
    removed = 0
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, raw_path FROM raw_documents WHERE fetched_at < :c AND raw_path IS NOT NULL"
                ),
                {"c": cutoff},
            )
        ).all()
        for r in rows:
            try:
                Path(r.raw_path).unlink(missing_ok=True)
                removed += 1
            except OSError:
                stats.errors += 1
        await s.execute(
            text("UPDATE raw_documents SET raw_path = NULL WHERE fetched_at < :c"), {"c": cutoff}
        )
        # drop news full text, keep chunks/metadata
        await s.execute(
            text(
                "UPDATE documents SET text = '' WHERE trust_level = 'news' AND created_at < :c AND text <> ''"
            ),
            {"c": cutoff},
        )
        # downsample flow observations older than N days to hourly means
        flow_cut = utcnow() - timedelta(days=settings.flow_downsample_after_days)
        await s.execute(
            text(
                """
                WITH agg AS (
                  DELETE FROM traffic_flow_obs WHERE resolution = 'raw' AND observed_at < :c
                  RETURNING segment_id, observed_at, current_speed, free_flow_speed,
                            current_travel_time, free_flow_travel_time, confidence, road_closure
                )
                INSERT INTO traffic_flow_obs (segment_id, observed_at, current_speed, free_flow_speed,
                    current_travel_time, free_flow_travel_time, confidence, road_closure, resolution)
                SELECT segment_id, date_trunc('hour', observed_at), avg(current_speed), avg(free_flow_speed),
                       avg(current_travel_time), avg(free_flow_travel_time), avg(confidence), bool_or(road_closure), 'hourly'
                FROM agg GROUP BY segment_id, date_trunc('hour', observed_at)
                """
            ),
            {"c": flow_cut},
        )
    stats.details["raw_files_removed"] = removed


JOBS: dict[str, tuple[str, Callable[[JobStats], Awaitable[None]]]] = {
    "dtp": ("dtp_advisories", job_dtp_incremental),
    "news": ("google_news", job_google_news),
    "rss": ("extra_rss", job_extra_rss),
    "tomtom": ("tomtom", job_tomtom_tick),
    "weather": ("weather", job_weather),
    "gazetteer": ("gazetteer", job_gazetteer),
    "expire": ("system", job_expire),
    "retention": ("system", job_retention),
}
