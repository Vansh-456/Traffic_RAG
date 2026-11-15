"""Scheduler entrypoint (``python -m app.worker``).

Runs every ingestion/maintenance job on its cron schedule (Asia/Kolkata),
plus an event-driven alert matcher subscribed to Redis ``events:new_chunk``.
Job failures are contained by ``run_job``; the worker never crashes on them.
"""

from __future__ import annotations

import asyncio
import json
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.config import get_settings
from app.db.session import session_scope
from app.ingestion import jobs
from app.ingestion.geocoding import refresh_geocoder
from app.observability.logging import configure_logging, get_logger
from app.providers.llm import attach_redis
from app.redis_client import CHANNEL_NEW_CHUNK, get_redis

log = get_logger("worker")


async def bootstrap() -> None:
    """Idempotent first-run setup: sources, gazetteer seed, segments, events."""
    async with session_scope() as s:
        await jobs.ensure_sources(s)
        places = (await s.execute(text("SELECT count(*) FROM places"))).scalar_one()
        segments = (await s.execute(text("SELECT count(*) FROM segments"))).scalar_one()
    if places == 0:
        log.info("empty gazetteer; loading seed aliases (OSM import runs on schedule / CLI)")
        await jobs.run_job("gazetteer", "seed", lambda st: jobs.job_gazetteer(st, with_osm=False))
    if segments == 0:
        log.info("seeding segments and events", extra=await jobs.seed_segments_and_events())
    async with session_scope() as s:
        await refresh_geocoder(s, get_redis())


async def alert_listener() -> None:
    """React to new chunks immediately (in addition to the 5-minute sweep)."""
    while True:
        try:
            pubsub = get_redis().pubsub()
            await pubsub.subscribe(CHANNEL_NEW_CHUNK)
            from app.alerts.matcher import dispatch, match_chunks

            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                payload = json.loads(msg["data"])
                result = await dispatch(await match_chunks(payload.get("chunk_ids", [])))
                if result["candidates"]:
                    log.info("event-driven alerts", extra=result)
        except Exception as exc:
            log.warning("alert listener error; reconnecting", extra={"err": str(exc)[:200]})
            await asyncio.sleep(5)


async def _alerts_job(stats: jobs.JobStats) -> None:
    from app.alerts.matcher import run_alerts_cycle

    stats.details = await run_alerts_cycle()


async def _eval_job(stats: jobs.JobStats) -> None:
    """Nightly smoke eval in a subprocess (it switches to the isolated eval DB)."""
    import sys

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.cli",
        "eval",
        "run",
        "--subset",
        "smoke",
        "--label",
        "nightly-smoke",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    stats.details = {"exit_code": proc.returncode, "tail": out.decode(errors="ignore")[-1500:]}
    if proc.returncode:
        stats.errors += 1


def schedule(scheduler: AsyncIOScheduler) -> None:
    s = get_settings()
    tz = s.timezone
    plan = [
        ("dtp", s.schedule_dtp),
        ("news", s.schedule_news),
        ("rss", s.schedule_rss),
        ("tomtom", s.schedule_tomtom_tick),
        ("weather", s.schedule_weather),
        ("gazetteer", s.schedule_gazetteer),
        ("expire", s.schedule_expire),
        ("retention", s.schedule_retention),
    ]
    for name, cron in plan:
        source_id, fn = jobs.JOBS[name]
        scheduler.add_job(
            jobs.run_job,
            CronTrigger.from_crontab(cron, timezone=tz),
            args=[source_id, name, fn],
            id=name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
    scheduler.add_job(
        jobs.run_job,
        CronTrigger.from_crontab(s.schedule_alerts, timezone=tz),
        args=["system", "alerts", _alerts_job],
        id="alerts",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.run_job,
        CronTrigger.from_crontab(s.schedule_eval, timezone=tz),
        args=["system", "eval-smoke", _eval_job, 7200],
        id="eval",
        max_instances=1,
        coalesce=True,
    )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    attach_redis(get_redis())
    await bootstrap()
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    schedule(scheduler)
    scheduler.start()
    log.info(
        "worker started",
        extra={"jobs": [j.id for j in scheduler.get_jobs()], "features": settings.feature_flags()},
    )
    # kick off a first round of fast jobs so a fresh install fills quickly
    startup = [
        asyncio.create_task(jobs.run_job(*jobs.JOBS[n][:1], n, jobs.JOBS[n][1]))
        for n in ("weather", "dtp", "news", "tomtom")
    ]
    listener = asyncio.create_task(alert_listener())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    await stop.wait()
    listener.cancel()
    for task in startup:
        task.cancel()
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
