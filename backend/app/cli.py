"""Typer CLI: ``copilot ingest ...``, ``copilot eval ...``, ``copilot db seed``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import typer

from app.config import get_settings
from app.observability.logging import configure_logging

app = typer.Typer(help="Delhi Traffic Copilot management CLI", no_args_is_help=True)
ingest = typer.Typer(help="Ingestion commands", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluation commands", no_args_is_help=True)
db_app = typer.Typer(help="Database commands", no_args_is_help=True)
alerts_app = typer.Typer(help="Alert commands", no_args_is_help=True)
app.add_typer(ingest, name="ingest")
app.add_typer(eval_app, name="eval")
app.add_typer(db_app, name="db")
app.add_typer(alerts_app, name="alerts")


def _run(coro: Coroutine[Any, Any, Any], background: bool = True) -> Any:
    configure_logging(get_settings().log_level)

    async def wrapped() -> Any:
        from app.providers.llm import attach_redis, get_llm
        from app.redis_client import get_redis

        attach_redis(get_redis())
        get_llm().background = background
        from app.db.session import session_scope
        from app.ingestion.geocoding import refresh_geocoder
        from app.ingestion.jobs import ensure_sources

        async with session_scope() as s:
            await ensure_sources(s)
            await refresh_geocoder(s, get_redis())
        return await coro

    return asyncio.run(wrapped())


def _print(stats: Any) -> None:
    typer.echo(
        json.dumps(
            stats if isinstance(stats, dict) else stats.__dict__,
            default=str,
            indent=2,
            ensure_ascii=False,
        )
    )


@ingest.command("backfill-dtp")
def backfill_dtp(
    start: int = typer.Option(1, help="lowest node id"),
    limit: int | None = typer.Option(None, help="max nodes to scan (newest first)"),
    max_id: int | None = typer.Option(None, help="highest node id (default: newest on the site)"),
) -> None:
    """Crawl all historical DTP advisory nodes (+ attached PDFs)."""
    from app.ingestion.jobs import job_dtp_backfill, run_job

    _print(
        _run(
            run_job(
                "dtp_advisories",
                "backfill",
                lambda st: job_dtp_backfill(st, start=start, limit=limit, max_id=max_id),
                lock_ttl=6 * 3600,
            )
        )
    )


@ingest.command("backfill-news")
def backfill_news(days: int = typer.Option(180)) -> None:
    """GDELT backfill of Delhi traffic news."""
    from app.ingestion.jobs import job_gdelt_backfill, run_job

    _print(
        _run(
            run_job(
                "gdelt", "backfill", lambda st: job_gdelt_backfill(st, days=days), lock_ttl=6 * 3600
            )
        )
    )


@ingest.command("gazetteer")
def gazetteer(osm: bool = typer.Option(True, help="also import from OSM Overpass")) -> None:
    from app.ingestion.jobs import job_gazetteer, run_job, seed_segments_and_events

    async def go() -> dict[str, Any]:
        stats = await run_job("gazetteer", "refresh", lambda st: job_gazetteer(st, with_osm=osm))
        return {"gazetteer": stats.__dict__, "seeded": await seed_segments_and_events()}

    _print(_run(go()))


@ingest.command("run-once")
def run_once(
    source: str = typer.Argument(
        ..., help="dtp|news|rss|tomtom|weather|gazetteer|expire|retention"
    ),
) -> None:
    from app.ingestion.jobs import JOBS, run_job

    if source not in JOBS:
        raise typer.BadParameter(f"choose from {sorted(JOBS)}")
    source_id, fn = JOBS[source]
    _print(_run(run_job(source_id, source, fn)))


@db_app.command("seed")
def seed(
    backfill_limit: int = typer.Option(400, help="DTP nodes to scan on first seed (0 = skip)"),
) -> None:
    """Make a fresh deployment usable: sources, gazetteer seed, segments, events, weather, recent advisories + news."""
    from app.ingestion import jobs

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["gazetteer"] = (
            await jobs.run_job(
                "gazetteer", "seed", lambda st: jobs.job_gazetteer(st, with_osm=False)
            )
        ).__dict__
        out["segments_events"] = await jobs.seed_segments_and_events()
        out["weather"] = (await jobs.run_job("weather", "seed", jobs.job_weather)).__dict__
        out["dtp"] = (
            await jobs.run_job("dtp_advisories", "seed", jobs.job_dtp_incremental)
        ).__dict__
        if backfill_limit:
            out["dtp_backfill"] = (
                await jobs.run_job(
                    "dtp_advisories",
                    "seed-backfill",
                    lambda st: jobs.job_dtp_backfill(st, limit=backfill_limit),
                    lock_ttl=4 * 3600,
                )
            ).__dict__
        out["news"] = (await jobs.run_job("google_news", "seed", jobs.job_google_news)).__dict__
        out["tomtom"] = (await jobs.run_job("tomtom", "seed", jobs.job_tomtom_tick)).__dict__
        return out

    _print(_run(go()))


@alerts_app.command("simulate")
def simulate(
    lat: float = typer.Option(...),
    lon: float = typer.Option(...),
    title: str = typer.Option("Simulated road closure"),
    hours: int = 3,
) -> None:
    """Inject a simulated advisory at a point and fire matching route alerts."""
    from datetime import timedelta

    from sqlalchemy import text

    from app.alerts.matcher import dispatch, match_chunks
    from app.db.session import session_scope
    from app.ingestion.pipeline import DocumentDraft, process
    from app.timeutil import utcnow

    async def go() -> dict[str, Any]:
        now = utcnow()
        draft = DocumentDraft(
            source_id="system",
            url=f"simulated:{now.timestamp()}",
            title=title,
            trust_level="official",
            content_type="text",
            body_text=f"{title}. Expect diversions near the marked location.",
            published_at=now,
            authority="Simulation",
            valid_from_hint=now,
            valid_to_hint=now + timedelta(hours=hours),
            metadata={"simulated": True},
        )
        async with session_scope() as s:
            res = await process(s, draft)
            await s.execute(
                text(
                    "UPDATE chunks SET geom = ST_SetSRID(ST_MakePoint(:lon,:lat),4326) WHERE id = ANY(:ids)"
                ),
                {"lon": lon, "lat": lat, "ids": res.chunk_ids},
            )
        return {"chunks": res.chunk_ids, **(await dispatch(await match_chunks(res.chunk_ids)))}

    _print(_run(go()))


@eval_app.command("run")
def eval_run(
    subset: str = typer.Option("full", help="full|smoke|retrieval"),
    ablation: list[str] = typer.Option(
        [],
        help="no-rerank no-hybrid no-geo-filter no-freshness no-query-rewrite no-question-index no-verification, or 'all'",
    ),
    label: str | None = None,
    gate: bool = typer.Option(False, help="fail (exit 1) if metrics regress vs baseline"),
    update_baseline: bool = False,
) -> None:
    from app.eval.runner import run_ablations, run_eval

    if ablation:
        report = _run(run_ablations(ablation, subset=subset), background=False)
    else:
        report = _run(
            run_eval(
                subset=subset, label=label or subset, gate=gate, update_baseline=update_baseline
            ),
            background=False,
        )
    _print(report)
    if gate and report.get("gate", {}).get("passed") is False:
        raise typer.Exit(code=1)


@eval_app.command("generate-questions")
def generate_questions(n: int = typer.Option(160), seed: int = 7) -> None:
    """Draft eval questions from ingested advisories (LLM) for human review."""
    from app.eval.dataset import generate_questions as gen

    _print(_run(gen(n=n, seed=seed)))


@eval_app.command("export")
def export_questions() -> None:
    """Write approved eval questions to data/seed/eval_questions.jsonl."""
    from app.eval.dataset import export_approved

    _print(_run(export_approved()))


@eval_app.command("import")
def import_questions() -> None:
    """Load data/seed/eval_questions.jsonl into the review table."""
    from app.eval.dataset import import_jsonl

    _print(_run(import_jsonl()))


if __name__ == "__main__":
    app()
