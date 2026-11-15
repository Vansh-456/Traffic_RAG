"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.config import get_settings
from app.observability.logging import configure_logging, get_logger, request_id_var
from app.observability.metrics import REQUEST_LATENCY
from app.providers.llm import attach_redis
from app.redis_client import get_redis

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        await get_redis().ping()
        attach_redis(get_redis())
    except Exception as exc:
        log.warning("redis unavailable at startup", extra={"err": str(exc)})
    flags = settings.feature_flags()
    for feature, enabled in flags.items():
        if not enabled:
            log.info("feature disabled (missing config)", extra={"feature": feature})
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Delhi Traffic Copilot API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url, "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            REQUEST_LATENCY.labels(request.method, path, str(status)).observe(
                time.perf_counter() - started
            )
            request_id_var.reset(token)

    app.include_router(health.router)
    return app


app = create_app()
