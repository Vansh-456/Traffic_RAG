"""Shared async Redis connection and small helpers (locks, pub/sub channels)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from redis.asyncio import Redis

from app.config import get_settings

CHANNEL_NEW_CHUNK = "events:new_chunk"
CHANNEL_NEW_INCIDENT = "events:new_incident"


@lru_cache
def get_redis() -> Redis[bytes]:
    return Redis.from_url(get_settings().redis_url, decode_responses=False)


class LockNotAcquiredError(RuntimeError):
    pass


@asynccontextmanager
async def job_lock(
    name: str, ttl_seconds: int = 1800, redis: Redis[bytes] | None = None
) -> AsyncIterator[None]:
    """Non-blocking distributed lock so scheduled jobs never overlap."""
    r = redis or get_redis()
    lock = r.lock(f"lock:job:{name}", timeout=ttl_seconds, blocking=False)
    if not await lock.acquire():
        raise LockNotAcquiredError(name)
    try:
        yield
    finally:
        try:
            await lock.release()
        except Exception:  # lock may have expired
            pass
