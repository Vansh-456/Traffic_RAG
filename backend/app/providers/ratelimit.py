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

    async def acquire(self, key: str, max_wait: float = 90.0, limit: int | None = None) -> None:
        """Wait for a slot; raises TimeoutError after `max_wait` seconds."""
        deadline = time.monotonic() + max_wait
        while True:
            wait = await self._try_acquire(key, limit or self.limit)
            if wait <= 0:
                return
            if time.monotonic() + wait > deadline:
                raise TimeoutError(f"rate limit wait for {key} exceeds {max_wait}s")
            log.info(
                "llm rate limit reached; waiting", extra={"key": key, "wait_s": round(wait, 2)}
            )
            await asyncio.sleep(wait)

    async def _try_acquire(self, key: str, limit: int | None = None) -> float:
        limit = limit or self.limit
        if self.redis is not None:
            try:
                return await self._try_redis(key, limit)
            except Exception as exc:  # pragma: no cover - redis outage path
                log.warning("rate limiter redis unavailable, using local", extra={"err": str(exc)})
                self.redis = None
        return await self._try_local(key, limit)

    async def _try_local(self, key: str, limit: int) -> float:
        async with self._lock:
            now = time.monotonic()
            window = self._local.setdefault(key, deque())
            while window and now - window[0] >= WINDOW_SECONDS:
                window.popleft()
            if len(window) < limit:
                window.append(now)
                return 0.0
            return WINDOW_SECONDS - (now - window[0]) + 0.05

    async def _try_redis(self, key: str, limit: int) -> float:
        assert self.redis is not None
        rkey = f"ratelimit:llm:{key}"
        now = time.time()
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)
        pipe.zcard(rkey)
        _, count = await pipe.execute()
        if int(count) < limit:
            await self.redis.zadd(rkey, {f"{now}:{uuid.uuid4().hex[:8]}": now})
            await self.redis.expire(rkey, int(WINDOW_SECONDS) + 5)
            return 0.0
        oldest = await self.redis.zrange(rkey, 0, 0, withscores=True)
        if not oldest:
            return 0.1
        return max(0.05, WINDOW_SECONDS - (now - float(oldest[0][1])) + 0.05)


class TokenBudget:
    """Per-model tokens-per-minute window (Groq free tier: 8,000 TPM per model).

    Calls reserve their estimated tokens (prompt + max_tokens, which is what
    Groq counts) before being sent. `wait_seconds` tells the caller how long
    the model would need to free that budget, so the LLM client can switch to
    a fallback model instead of queueing. Provider "retry after N s" hints are
    applied as a cooldown via `block`.
    """

    def __init__(self, limit_per_minute: int, redis: Redis[bytes] | None = None) -> None:
        self.limit = max(1, limit_per_minute)
        self.redis = redis
        self._local: dict[str, deque[tuple[float, int]]] = {}
        self._blocked: dict[str, float] = {}

    async def _entries(self, key: str, now: float) -> list[tuple[float, int]]:
        if self.redis is not None:
            try:
                rkey = f"tpm:{key}"
                await self.redis.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)
                raw = await self.redis.zrange(rkey, 0, -1, withscores=True)
                return [(float(score), int(member.decode().split(":")[1])) for member, score in raw]
            except Exception:
                self.redis = None
        window = self._local.setdefault(key, deque())
        while window and now - window[0][0] >= WINDOW_SECONDS:
            window.popleft()
        return list(window)

    async def _blocked_for(self, key: str, now: float) -> float:
        if self.redis is not None:
            try:
                ttl = await self.redis.pttl(f"tpm:block:{key}")
                return max(0.0, ttl / 1000) if ttl and ttl > 0 else 0.0
            except Exception:
                self.redis = None
        return max(0.0, self._blocked.get(key, 0.0) - now)

    async def wait_seconds(self, key: str, tokens: int, limit: int | None = None) -> float:
        cap = limit or self.limit
        now = time.time()
        blocked = await self._blocked_for(key, now)
        entries = await self._entries(key, now)
        used = sum(t for _, t in entries)
        if used + tokens <= cap:
            return blocked
        # earliest moment enough old reservations expire to fit this call
        need = used + tokens - cap
        freed = 0
        for ts, t in sorted(entries):
            freed += t
            if freed >= need:
                return max(blocked, ts + WINDOW_SECONDS - now + 0.05)
        return max(blocked, WINDOW_SECONDS)

    async def reserve(self, key: str, tokens: int) -> None:
        now = time.time()
        if self.redis is not None:
            try:
                rkey = f"tpm:{key}"
                await self.redis.zadd(rkey, {f"{now}:{tokens}:{uuid.uuid4().hex[:6]}": now})
                await self.redis.expire(rkey, int(WINDOW_SECONDS) + 5)
                return
            except Exception:
                self.redis = None
        self._local.setdefault(key, deque()).append((now, tokens))

    async def block(self, key: str, seconds: float) -> None:
        seconds = min(max(seconds, 0.5), 60.0)
        if self.redis is not None:
            try:
                await self.redis.set(f"tpm:block:{key}", "1", px=int(seconds * 1000))
                return
            except Exception:
                self.redis = None
        self._blocked[key] = time.time() + seconds
