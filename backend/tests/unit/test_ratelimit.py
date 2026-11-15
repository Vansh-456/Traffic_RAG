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


async def test_background_limit_leaves_headroom():
    redis = fakeredis.aioredis.FakeRedis()
    rl = RateLimiter(5, redis)
    for _ in range(2):
        assert await rl._try_acquire("m", limit=2) == 0  # background share
    assert await rl._try_acquire("m", limit=2) > 0  # background must wait...
    assert await rl._try_acquire("m") == 0  # ...while interactive still has room


async def test_token_budget_wait_and_block():
    from app.providers.ratelimit import TokenBudget

    tb = TokenBudget(1000, fakeredis.aioredis.FakeRedis())
    assert await tb.wait_seconds("groq/x", 600) == 0
    await tb.reserve("groq/x", 600)
    assert await tb.wait_seconds("groq/x", 300) == 0
    assert 55 < await tb.wait_seconds("groq/x", 500) <= 60.1  # must wait for the first reservation
    await tb.block("groq/y", 5)
    assert 4 < await tb.wait_seconds("groq/y", 10) <= 5
