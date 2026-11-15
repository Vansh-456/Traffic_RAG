"""Dense retrieval over chunk embeddings and the hypothetical-question index."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retrieval.filters import FilterSpec


def vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def dense_search(
    session: AsyncSession, query_vec: list[float], spec: FilterSpec, limit: int = 30
) -> list[tuple[int, float]]:
    """Top chunks by cosine similarity → [(chunk_id, similarity)]."""
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    rows = await session.execute(
        text(
            f"""
            SELECT c.id, 1 - (e.embedding <=> CAST(:qv AS vector)) AS sim
            FROM chunk_embeddings e JOIN chunks c ON c.id = e.chunk_id
            WHERE {spec.where()}
            ORDER BY e.embedding <=> CAST(:qv AS vector)
            LIMIT :limit
            """
        ),
        {**spec.params(), "qv": vec_literal(query_vec), "limit": limit},
    )
    return [(int(r.id), float(r.sim)) for r in rows]


async def question_search(
    session: AsyncSession, query_vec: list[float], spec: FilterSpec, limit: int = 20
) -> list[tuple[int, float, str]]:
    """Match the query against hypothetical questions → [(chunk_id, sim, question)]."""
    rows = await session.execute(
        text(
            f"""
            SELECT c.id, 1 - (q.embedding <=> CAST(:qv AS vector)) AS sim, q.question
            FROM question_embeddings q JOIN chunks c ON c.id = q.chunk_id
            WHERE {spec.where()}
            ORDER BY q.embedding <=> CAST(:qv AS vector)
            LIMIT :limit
            """
        ),
        {**spec.params(), "qv": vec_literal(query_vec), "limit": limit * 4},
    )
    hits: dict[int, tuple[int, float, str]] = {}
    for r in rows:  # keep best question per chunk (rows arrive best-first)
        hits.setdefault(int(r.id), (int(r.id), float(r.sim), r.question))
    return list(hits.values())[:limit]
