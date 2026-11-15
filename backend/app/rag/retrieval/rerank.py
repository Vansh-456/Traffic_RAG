"""Cross-encoder rerank stage (top-N fused candidates → scores in [0,1])."""

from __future__ import annotations

from app.providers.reranker import Reranker, get_reranker


async def rerank(
    query: str, passages: list[tuple[int, str]], reranker: Reranker | None = None
) -> dict[int, float]:
    reranker = reranker or get_reranker()
    if not passages:
        return {}
    scores = await reranker.score(query, [p for _, p in passages])
    return {cid: float(s) for (cid, _), s in zip(passages, scores, strict=True)}
