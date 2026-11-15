"""Batch embedding of chunk texts and their hypothetical questions."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChunkEmbedding, QuestionEmbedding
from app.providers.embeddings import Embedder, get_embedder


async def embed_chunk_texts(
    session: AsyncSession,
    items: list[tuple[int, str]],
    embedder: Embedder | None = None,
) -> dict[int, list[float]]:
    """Embed `(chunk_id, text_with_context)` pairs and upsert vectors."""
    if not items:
        return {}
    embedder = embedder or get_embedder()
    vectors = await embedder.embed([t for _, t in items])
    out: dict[int, list[float]] = {}
    for (chunk_id, _), vec in zip(items, vectors, strict=True):
        stmt = insert(ChunkEmbedding).values(chunk_id=chunk_id, model=embedder.name, embedding=vec)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ChunkEmbedding.chunk_id],
            set_={"embedding": stmt.excluded.embedding, "model": stmt.excluded.model},
        )
        await session.execute(stmt)
        out[chunk_id] = vec
    return out


async def embed_questions(
    session: AsyncSession,
    chunk_id: int,
    questions: list[str],
    embedder: Embedder | None = None,
) -> int:
    """Replace the question-index entries for one chunk."""
    embedder = embedder or get_embedder()
    await session.execute(delete(QuestionEmbedding).where(QuestionEmbedding.chunk_id == chunk_id))
    questions = [q.strip() for q in questions if q and q.strip()][:6]
    if not questions:
        return 0
    vectors = await embedder.embed(questions)
    session.add_all(
        QuestionEmbedding(chunk_id=chunk_id, question=q, embedding=v)
        for q, v in zip(questions, vectors, strict=True)
    )
    return len(questions)
