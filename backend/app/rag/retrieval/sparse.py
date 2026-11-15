"""Sparse retrieval: Postgres full-text (english + simple) and trigram names."""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retrieval.filters import FilterSpec

_STOP = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "any",
    "what",
    "which",
    "how",
    "near",
    "there",
    "today",
    "now",
    "right",
    "will",
    "be",
    "to",
    "of",
    "in",
    "on",
    "for",
    "and",
    "or",
    "i",
    "me",
    "kya",
    "hai",
    "ke",
    "ki",
    "ka",
    "kaisa",
    "rahega",
    "paas",
    "aaj",
    "kal",
}


def or_tsquery(text_: str) -> str:
    """Build a permissive OR tsquery string ('ring:* | road:* | ito')."""
    words = [w for w in re.findall(r"[\wऀ-ॿ]+", text_.lower()) if w not in _STOP and len(w) > 1]
    words = list(dict.fromkeys(words))[:16]
    return " | ".join(f"{w}:*" if len(w) > 3 and w.isascii() else w for w in words)


async def fulltext_search(
    session: AsyncSession, query: str, spec: FilterSpec, limit: int = 30
) -> list[tuple[int, float]]:
    tsq = or_tsquery(query)
    if not tsq:
        return []
    rows = await session.execute(
        text(
            f"""
            WITH q AS (
              SELECT to_tsquery('english', :tsq) AS qe, to_tsquery('simple', :tsq) AS qs
            )
            SELECT c.id,
                   ts_rank_cd(c.tsv_en, q.qe, 32) + ts_rank_cd(c.tsv_simple, q.qs, 32) AS rank
            FROM chunks c, q
            WHERE {spec.where()} AND (c.tsv_en @@ q.qe OR c.tsv_simple @@ q.qs)
            ORDER BY rank DESC
            LIMIT :limit
            """
        ),
        {**spec.params(), "tsq": tsq, "limit": limit},
    )
    return [(int(r.id), float(r.rank)) for r in rows]


async def trigram_search(
    session: AsyncSession, names: list[str], spec: FilterSpec, limit: int = 30
) -> list[tuple[int, float]]:
    """Fuzzy place/road name hits in chunk text (handles spelling variants)."""
    names = [n.lower() for n in names if len(n) >= 3][:6]
    if not names:
        return []
    rows = await session.execute(
        text(
            f"""
            SELECT c.id, max(word_similarity(n, lower(c.text))) AS sim
            FROM chunks c, unnest(CAST(:names AS text[])) AS n
            WHERE {spec.where()} AND n <% lower(c.text)
            GROUP BY c.id
            ORDER BY sim DESC
            LIMIT :limit
            """
        ),
        {**spec.params(), "names": names, "limit": limit},
    )
    return [(int(r.id), float(r.sim)) for r in rows]
