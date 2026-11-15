"""Semantic answer cache in Redis (Section 6.1).

Entries are grouped by bucket = intent | resolved places | time bucket, so a
hit requires the same resolved location and time bucket *and* query cosine
≥ ``CACHE_SIM_THRESHOLD``. TTL depends on intent: live 5 min, today's
advisories 30 min, historical 24 h.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings
from app.observability.logging import get_logger
from app.observability.metrics import CACHE_LOOKUPS
from app.providers.embeddings import cosine

log = get_logger(__name__)

TTL_SECONDS = {
    "live_conditions": 300,
    "cause_explanation": 300,
    "advisory_lookup": 1800,
    "plan_trip": 1800,
    "general_info": 3600,
    "historical_lookup": 86400,
}
MAX_ENTRIES_PER_BUCKET = 50


def normalize_query(q: str) -> str:
    return " ".join(q.lower().split())


def _key(bucket: str) -> str:
    return "semcache:" + hashlib.sha1(bucket.encode()).hexdigest()


class SemanticCache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.threshold = get_settings().cache_sim_threshold

    async def lookup(self, bucket: str, query_vec: list[float]) -> tuple[dict[str, Any] | None, float]:
        try:
            raw = await self.redis.lrange(_key(bucket), 0, MAX_ENTRIES_PER_BUCKET)  # type: ignore[misc]
        except Exception as exc:
            log.warning("cache lookup failed", extra={"err": str(exc)})
            CACHE_LOOKUPS.labels("error").inc()
            return None, 0.0
        best: tuple[dict[str, Any] | None, float] = (None, 0.0)
        for item in raw:
            entry = json.loads(item)
            sim = cosine(query_vec, entry["vec"])
            if sim > best[1]:
                best = (entry["payload"], sim)
        if best[0] is not None and best[1] >= self.threshold:
            CACHE_LOOKUPS.labels("hit").inc()
            return best
        CACHE_LOOKUPS.labels("miss").inc()
        return None, best[1]

    async def store(self, bucket: str, intent: str, query_vec: list[float], payload: dict[str, Any]) -> None:
        ttl = TTL_SECONDS.get(intent)
        if not ttl:
            return
        key = _key(bucket)
        try:
            await self.redis.lpush(key, json.dumps({"vec": [round(x, 5) for x in query_vec], "payload": payload}, default=str))  # type: ignore[misc]
            await self.redis.ltrim(key, 0, MAX_ENTRIES_PER_BUCKET - 1)  # type: ignore[misc]
            await self.redis.expire(key, ttl)
        except Exception as exc:
            log.warning("cache store failed", extra={"err": str(exc)})
