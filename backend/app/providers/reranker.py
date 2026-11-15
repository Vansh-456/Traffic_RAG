"""Reranker providers (cross-encoder style relevance scores in [0, 1]).

- ``local``: BAAI/bge-reranker-v2-m3 cross-encoder (``local-ml`` extra)
- ``cohere``: Cohere Rerank v2 multilingual API
- ``lexical``: dependency-free scorer (token overlap + char trigram Jaccard
  + phrase bonus); used in dev/CI and as an automatic fallback.
"""

from __future__ import annotations

import asyncio
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache

import httpx

from app.config import Settings, get_settings
from app.observability.logging import get_logger
from app.providers.embeddings import normalize_text

log = get_logger(__name__)

_STOP = {
    "the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "or", "any", "what",
    "near", "at", "be", "will", "there", "today", "now", "kya", "ke", "ki", "ka", "hai", "me",
    "mein", "paas", "को", "का", "की", "के", "में", "है", "पर", "से",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\wऀ-ॿ]+", normalize_text(text)) if t not in _STOP]


def _trigrams(text: str) -> set[str]:
    s = f"  {normalize_text(text)} "
    return {s[i : i + 3] for i in range(len(s) - 2)}


class Reranker(ABC):
    name: str = "base"

    @abstractmethod
    async def score(self, query: str, passages: list[str]) -> list[float]:
        """Return a relevance score in [0,1] for each passage (same order)."""


class LexicalReranker(Reranker):
    name = "lexical-v1"

    async def score(self, query: str, passages: list[str]) -> list[float]:
        q_tokens = set(_tokens(query))
        q_tri = _trigrams(query)
        out: list[float] = []
        for passage in passages:
            p_tokens = set(_tokens(passage))
            coverage = len(q_tokens & p_tokens) / max(1, len(q_tokens))
            p_tri = _trigrams(passage[:3000])
            tri = len(q_tri & p_tri) / max(1, len(q_tri))
            raw = 0.6 * coverage + 0.4 * tri
            out.append(round(min(1.0, raw), 4))
        return out


class LocalReranker(Reranker):
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.name = model_name
        self._model: object | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> object:
        async with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import CrossEncoder
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError("RERANKER_PROVIDER=local needs the local-ml extra") from exc
                self._model = await asyncio.to_thread(CrossEncoder, self.model_name, max_length=512)
            return self._model

    async def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        model = await self._load()

        def _run() -> list[float]:
            logits = model.predict([(query, p) for p in passages])  # type: ignore[attr-defined]
            return [1.0 / (1.0 + math.exp(-float(x))) for x in logits]

        return await asyncio.to_thread(_run)


class CohereReranker(Reranker):
    URL = "https://api.cohere.com/v2/rerank"

    def __init__(self, api_key: str, model: str = "rerank-multilingual-v3.0") -> None:
        self.api_key = api_key
        self.model = model
        self.name = f"cohere/{model}"
        self.fallback = LexicalReranker()

    async def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    self.URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "query": query, "documents": passages},
                )
                resp.raise_for_status()
            scores = [0.0] * len(passages)
            for item in resp.json()["results"]:
                scores[item["index"]] = float(item["relevance_score"])
            return scores
        except Exception as exc:
            log.warning("cohere rerank failed; lexical fallback", extra={"err": str(exc)[:200]})
            return await self.fallback.score(query, passages)


def build_reranker(settings: Settings) -> Reranker:
    if settings.reranker_provider == "local":
        return LocalReranker(settings.reranker_model)
    if settings.reranker_provider == "cohere":
        if settings.cohere_api_key:
            return CohereReranker(settings.cohere_api_key)
        log.warning("RERANKER_PROVIDER=cohere but COHERE_API_KEY missing; using lexical reranker")
    return LexicalReranker()


@lru_cache
def get_reranker() -> Reranker:
    return build_reranker(get_settings())
