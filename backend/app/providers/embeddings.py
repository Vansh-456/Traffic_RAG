"""Embedding providers.

- ``local``: BAAI/bge-m3 via sentence-transformers (multilingual, 1024-d). Needs
  the ``local-ml`` extra / Docker build arg.
- ``api``: any LiteLLM embedding model (e.g. ``gemini/text-embedding-004``);
  vectors are truncated/zero-padded to ``EMBEDDING_DIM`` and re-normalised.
- ``hash``: deterministic character n-gram feature hashing. No downloads,
  handles Devanagari, good enough for dev/CI; quality is clearly lower.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np

from app.config import Settings, get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec if norm == 0 else vec / norm


def fit_dim(vec: list[float] | np.ndarray, dim: int) -> list[float]:
    arr = np.asarray(vec, dtype=np.float32)
    if arr.shape[0] > dim:
        arr = arr[:dim]
    elif arr.shape[0] < dim:
        arr = np.concatenate([arr, np.zeros(dim - arr.shape[0], dtype=np.float32)])
    return [float(x) for x in l2_normalize(arr)]


def cosine(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return 0.0 if denom == 0 else float(np.dot(va, vb) / denom)


class Embedder(ABC):
    """Embedding provider interface."""

    name: str = "base"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed documents/passages."""

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class HashEmbedder(Embedder):
    """Feature-hashed word unigrams/bigrams + char 3/4-grams, signed, L2-normalised."""

    name = "hash-ngram-v1"

    def _features(self, text: str) -> list[tuple[str, float]]:
        norm = normalize_text(text)
        words = re.findall(r"[\wऀ-ॿ]+", norm)
        feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in zip(words, words[1:], strict=False)]
        for w in words:
            padded = f"#{w}#"
            for n in (3, 4):
                feats += [(f"c{n}:{padded[i : i + n]}", 0.35) for i in range(len(padded) - n + 1)]
        return feats

    def _embed_one(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for feat, weight in self._features(text):
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign * weight
        # sublinear damping so long texts don't dominate
        vec = np.sign(vec) * np.log1p(np.abs(vec))
        return [float(x) for x in l2_normalize(vec)]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class LocalEmbedder(Embedder):
    """bge-m3 through sentence-transformers, loaded lazily, run in a thread."""

    def __init__(self, dim: int, model_name: str) -> None:
        super().__init__(dim)
        self.model_name = model_name
        self.name = model_name
        self._model: object | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> object:
        async with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError(
                        "EMBEDDING_PROVIDER=local needs the local-ml extra "
                        "(pip install '.[local-ml]' or build with INSTALL_LOCAL_ML=1)"
                    ) from exc
                log.info("loading embedding model", extra={"model": self.model_name})
                self._model = await asyncio.to_thread(SentenceTransformer, self.model_name)
            return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = await self._load()

        def _run() -> list[list[float]]:
            vecs = model.encode(texts, batch_size=16, normalize_embeddings=True)  # type: ignore[attr-defined]
            return [fit_dim(v, self.dim) for v in vecs]

        return await asyncio.to_thread(_run)


class ApiEmbedder(Embedder):
    """Hosted embeddings through LiteLLM."""

    def __init__(self, dim: int, model_name: str, api_key: str = "") -> None:
        super().__init__(dim)
        self.model_name = model_name
        self.api_key = api_key
        self.name = model_name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        out: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            kwargs = {"api_key": self.api_key} if self.api_key else {}
            resp = await litellm.aembedding(model=self.model_name, input=batch, **kwargs)
            out.extend(fit_dim(item["embedding"], self.dim) for item in resp.data)
        return out


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "local":
        return LocalEmbedder(settings.embedding_dim, settings.embedding_model)
    if settings.embedding_provider == "api":
        return ApiEmbedder(settings.embedding_dim, settings.embedding_model, settings.embedding_api_key)
    if settings.embedding_dim != 1024:
        log.warning("EMBEDDING_DIM differs from schema dim 1024")
    return HashEmbedder(settings.embedding_dim)


@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())
