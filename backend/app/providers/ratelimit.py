"""Per-model request rate limiter (sliding 60 s window).

Groq's free tier allows ~30 requests/minute per model. API and worker run in
separate processes, so the window is kept in Redis when available; if Redis
is unreachable we fall back to an in-process window (still protects a single
process).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque

from redis.asyncio import Redis

from app.observability.logging import get_logger

log = get_logger(__name__)

WINDOW_SECONDS = 60.0


class RateLimiter:
    """Blocks until a request slot for `key` is available."""

    def __init__(self, limit_per_minute: int, redis: Redis[bytes] | None = None) -> None:
        self.limit = max(1, limit_per_minute)
        self.redis = redis
        self._local: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, max_wait: float = 90.0) -> None:
        """Wait for a slot; raises TimeoutError after `max_wait` seconds."""
        deadline = time.monotonic() + max_wait
        while True:
            wait = await self._try_acquire(key)
            if wait <= 0:
                return
            if time.monotonic() + wait > deadline:
                raise TimeoutError(f"rate limit wait for {key} exceeds {max_wait}s")
            log.info(
                "llm rate limit reached; waiting", extra={"key": key, "wait_s": round(wait, 2)}
            )
            await asyncio.sleep(wait)

    async def _try_acquire(self, key: str) -> float:
        if self.redis is not None:
            try:
                return await self._try_redis(key)
            except Exception as exc:  # pragma: no cover - redis outage path
                log.warning("rate limiter redis unavailable, using local", extra={"err": str(exc)})
                self.redis = None
        return await self._try_local(key)

    async def _try_local(self, key: str) -> float:
        async with self._lock:
            now = time.monotonic()
            window = self._local.setdefault(key, deque())
            while window and now - window[0] >= WINDOW_SECONDS:
                window.popleft()
            if len(window) < self.limit:
                window.append(now)
                return 0.0
            return WINDOW_SECONDS - (now - window[0]) + 0.05

    async def _try_redis(self, key: str) -> float:
        assert self.redis is not None
        rkey = f"ratelimit:llm:{key}"
        now = time.time()
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)
        pipe.zcard(rkey)
        _, count = await pipe.execute()
        if int(count) < self.limit:
            await self.redis.zadd(rkey, {f"{now}:{uuid.uuid4().hex[:8]}": now})
            await self.redis.expire(rkey, int(WINDOW_SECONDS) + 5)
            return 0.0
        oldest = await self.redis.zrange(rkey, 0, 0, withscores=True)
        if not oldest:
            return 0.1
        return max(0.05, WINDOW_SECONDS - (now - float(oldest[0][1])) + 0.05)
