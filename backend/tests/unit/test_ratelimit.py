import time

import fakeredis.aioredis
import pytest

from app.providers.ratelimit import RateLimiter


async def test_local_limiter_allows_up_to_limit():
    rl = RateLimiter(3)
    for _ in range(3):
        assert await rl._try_acquire("m") == 0
    assert await rl._try_acquire("m") > 0


async def test_redis_limiter_shared_window():
    redis = fakeredis.aioredis.FakeRedis()
    a, b = RateLimiter(2, redis), RateLimiter(2, redis)
    assert await a._try_acquire("groq/x") == 0
    assert await b._try_acquire("groq/x") == 0
    assert await a._try_acquire("groq/x") > 0  # shared across "processes"
    assert await a._try_acquire("other") == 0


async def test_acquire_times_out():
    rl = RateLimiter(1)
    await rl.acquire("m")
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await rl.acquire("m", max_wait=0.2)
    assert time.monotonic() - started < 1
