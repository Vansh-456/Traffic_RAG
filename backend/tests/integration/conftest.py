"""Integration fixtures: an isolated `copilot_test` database + Redis db 15.

Requires `docker compose up db redis`. Tests are skipped (not failed) when the
services are unreachable. TEST_DATABASE_URL / TEST_REDIS_URL override defaults.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://copilot:copilot@localhost:5433/copilot_test"
)
TEST_REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")

os.environ["DATABASE_URL"] = TEST_DB
os.environ["REDIS_URL"] = TEST_REDIS
os.environ["APP_ENV"] = "test"
os.environ["GROQ_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["TOMTOM_API_KEY"] = ""
os.environ["RATE_LIMIT_PER_MIN"] = "1000"


def _prepare_db() -> str | None:
    try:
        import psycopg
    except ImportError:
        return "psycopg missing"
    base, _, name = TEST_DB.replace("+asyncpg", "").rpartition("/")
    try:
        with psycopg.connect(f"{base}/postgres", autocommit=True, connect_timeout=3) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                conn.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:
        return f"postgres unavailable: {exc}"
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    if res.returncode:
        return f"migration failed: {res.stderr[-500:]}"
    return None


_SKIP = _prepare_db()


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if _SKIP:
                item.add_marker(pytest.mark.skip(reason=_SKIP))


@pytest.fixture(autouse=True)
async def clean_state():
    """Truncate data tables and flush Redis before each test; reset cached clients."""
    if _SKIP:
        yield
        return
    from sqlalchemy import text

    from app.config import get_settings
    from app.db.session import get_engine, get_sessionmaker
    from app.ingestion import geocoding
    from app.redis_client import get_redis

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_redis.cache_clear()
    geocoding._geocoder = None
    async with get_engine().begin() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('alembic_version','spatial_ref_sys')"
                    )
                )
            )
            .scalars()
            .all()
        )
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await get_redis().flushdb()
    from app.db.session import session_scope
    from app.ingestion.jobs import ensure_sources
    from app.ingestion.sources.osm_gazetteer import upsert_seed

    async with session_scope() as s:
        await ensure_sources(s)
        await upsert_seed(s)
    yield
    await get_engine().dispose()
    await get_redis().aclose()
