"""Near-duplicate detection across sources.

Two chunks from documents of *different sources* published within ±48 h are
duplicates if their 64-bit SimHash differs in ≤ 3 bits *or* their embedding cosine is ≥
``DEDUP_SIM_THRESHOLD`` (default 0.92). Duplicates share a
``dedup_cluster_id``; one **canonical** member per cluster is chosen
(official > sensor > news, then the longest / most complete text). Retrieval
only returns canonical chunks; citations show "also reported by N sources".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

TRUST_RANK = {"official": 3, "sensor": 2, "news": 1}
WINDOW = timedelta(hours=48)
SIMHASH_MAX_DISTANCE = 3


def _shingles(text_: str, n: int = 3) -> list[str]:
    words = re.findall(r"[\wऀ-ॿ]+", text_.lower())
    if len(words) < n:
        return words
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]


def simhash64(text_: str) -> int:
    """Signed 64-bit SimHash (fits Postgres BIGINT)."""
    weights = [0] * 64
    for sh in _shingles(text_):
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    value = sum(1 << bit for bit in range(64) if weights[bit] > 0)
    return value - (1 << 64) if value >= (1 << 63) else value


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


@dataclass
class Member:
    chunk_id: int
    trust_level: str
    length: int


def choose_canonical(members: list[Member]) -> int:
    """Prefer official > sensor > news, then longest text, then oldest id."""
    best = max(members, key=lambda m: (TRUST_RANK.get(m.trust_level, 0), m.length, -m.chunk_id))
    return best.chunk_id


async def assign_cluster(
    session: AsyncSession,
    *,
    chunk_id: int,
    document_id: int,
    simhash: int,
    embedding: list[float],
    published_at: datetime | None,
) -> dict[str, object]:
    """Cluster a freshly embedded chunk; returns a small report for logs/internals."""
    threshold = get_settings().dedup_sim_threshold
    ref = published_at or datetime.now().astimezone()
    vec = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
    rows = (
        await session.execute(
            text(
                """
                SELECT c.id, c.simhash, c.dedup_cluster_id,
                       1 - (e.embedding <=> CAST(:vec AS vector)) AS sim
                FROM chunks c JOIN chunk_embeddings e ON e.chunk_id = c.id
                JOIN documents d ON d.id = c.document_id
                WHERE c.document_id <> :doc
                  AND d.source_id <> (SELECT source_id FROM documents WHERE id = :doc)
                  AND COALESCE(c.published_at, c.created_at) BETWEEN :lo AND :hi
                ORDER BY e.embedding <=> CAST(:vec AS vector)
                LIMIT 20
                """
            ),
            {"vec": vec, "doc": document_id, "lo": ref - WINDOW, "hi": ref + WINDOW},
        )
    ).all()
    match = None
    for r in rows:
        near_text = r.simhash is not None and hamming(int(r.simhash), simhash) <= SIMHASH_MAX_DISTANCE
        if near_text or float(r.sim) >= threshold:
            match = r
            break
    if match is None:
        await session.execute(
            text("UPDATE chunks SET dedup_cluster_id = id, is_canonical = true WHERE id = :id"),
            {"id": chunk_id},
        )
        return {"clustered": False}
    cluster_id = int(match.dedup_cluster_id or match.id)
    await session.execute(
        text("UPDATE chunks SET dedup_cluster_id = :cid WHERE id IN (:id, :mid)"),
        {"cid": cluster_id, "id": chunk_id, "mid": match.id},
    )
    members = [
        Member(r.id, r.trust_level, r.length)
        for r in (
            await session.execute(
                text(
                    "SELECT id, trust_level, length(text) AS length FROM chunks WHERE dedup_cluster_id = :cid"
                ),
                {"cid": cluster_id},
            )
        ).all()
    ]
    canonical = choose_canonical(members)
    await session.execute(
        text("UPDATE chunks SET is_canonical = (id = :canon) WHERE dedup_cluster_id = :cid"),
        {"canon": canonical, "cid": cluster_id},
    )
    log.info(
        "dedup cluster", extra={"cluster": cluster_id, "size": len(members), "canonical": canonical}
    )
    return {"clustered": True, "cluster_id": cluster_id, "size": len(members), "canonical": canonical}
