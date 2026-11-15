"""Liveness, readiness and Prometheus metrics endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_engine
from app.redis_client import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe: the process is up."""
    return {"status": "ok", "features": get_settings().feature_flags()}


@router.get("/ready")
async def ready(response: Response) -> dict[str, Any]:
    """Readiness probe: database (with extensions) and Redis reachable."""
    checks: dict[str, str] = {}
    try:
        async with get_engine().connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT extname FROM pg_extension WHERE extname IN ('vector','postgis','pg_trgm')"
                )
            )
            exts = {r[0] for r in rows}
        checks["db"] = "ok" if len(exts) == 3 else f"missing extensions: {exts}"
    except Exception as exc:
        checks["db"] = f"error: {type(exc).__name__}"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    response.status_code = 200 if ok else 503
    return {"status": "ok" if ok else "degraded", "checks": checks}


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
