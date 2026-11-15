"""Hybrid retrieval orchestration (Section 6.4).

filters → dense (per rewrite) + question index + full-text + trigram → RRF
→ canonical collapse → cross-encoder rerank (top 40) → final score with
freshness × trust × proximity → top k, with every stage's scores recorded.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingestion.geocoding import GeoMatch
from app.observability.metrics import STAGE_LATENCY
from app.providers.embeddings import Embedder, get_embedder
from app.providers.reranker import LexicalReranker, Reranker, get_reranker
from app.rag.retrieval import dense, freshness, sparse
from app.rag.retrieval.filters import build_filters
from app.rag.retrieval.fusion import rrf
from app.rag.retrieval.rerank import rerank
from app.rag.timewindow import TimeWindow


@dataclass
class Toggles:
    """Ablation switches (all on by default)."""

    rerank: bool = True
    hybrid: bool = True  # sparse + trigram lists
    geo_filter: bool = True
    freshness: bool = True
    question_index: bool = True


@dataclass
class Candidate:
    chunk_id: int
    document_id: int
    title: str
    url: str
    source_id: str
    trust_level: str
    publisher: str | None
    authority: str | None
    language: str
    published_at: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    event_type: str | None
    severity: str | None
    text: str
    text_with_context: str
    metadata: dict[str, Any]
    geom_geojson: dict[str, Any] | None
    distance_m: float | None
    also_reported_by: int
    ranks: dict[str, int] = field(default_factory=dict)
    rrf: float = 0.0
    rerank: float = 0.0
    freshness: float = 1.0
    trust: float = 1.0
    proximity: float = 1.0
    final: float = 0.0
    matched_question: str | None = None

    def trace(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "title": self.title[:140],
            "source": self.source_id,
            "trust_level": self.trust_level,
            "ranks": self.ranks,
            "rrf": round(self.rrf, 5),
            "rerank": round(self.rerank, 4),
            "freshness": round(self.freshness, 4),
            "trust": self.trust,
            "proximity": round(self.proximity, 4),
            "final": round(self.final, 4),
            "distance_m": round(self.distance_m) if self.distance_m is not None else None,
            "matched_question": self.matched_question,
        }


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    dropped: list[dict[str, Any]]
    trace: dict[str, Any]

    @property
    def best_score(self) -> float:
        return max((c.final for c in self.candidates), default=0.0)


_FETCH_SQL = """
SELECT c.id, c.document_id, d.title, d.url, d.source_id, c.trust_level, d.publisher, d.authority,
       c.language, c.published_at, c.valid_from, c.valid_to, c.event_type, c.severity, c.text,
       c.text_with_context, c.metadata, c.dedup_cluster_id,
       CASE WHEN c.geom IS NOT NULL THEN ST_AsGeoJSON(c.geom)::json END AS geojson,
       CASE WHEN c.geom IS NOT NULL AND CAST(:q_geom AS text) IS NOT NULL
            THEN ST_Distance(c.geom::geography, ST_GeomFromText(CAST(:q_geom AS text), 4326)::geography) END AS dist,
       (SELECT count(*) - 1 FROM chunks c2 WHERE c2.dedup_cluster_id = c.dedup_cluster_id) AS dupes
FROM chunks c JOIN documents d ON d.id = c.document_id
WHERE c.id = ANY(:ids)
"""


async def hybrid_search(
    session: AsyncSession,
    *,
    queries: list[str],
    locations: list[GeoMatch],
    window: TimeWindow | None,
    intent: str,
    now: datetime,
    k: int | None = None,
    toggles: Toggles | None = None,
    event_types: list[str] | None = None,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
) -> RetrievalResult:
    settings = get_settings()
    toggles = toggles or Toggles()
    k = k or settings.retrieval_top_k
    embedder = embedder or get_embedder()
    reranker = reranker or get_reranker()
    timings: dict[str, float] = {}

    def lap(stage: str, started: float) -> None:
        dt = time.perf_counter() - started
        timings[stage] = round(dt * 1000, 1)
        STAGE_LATENCY.labels(stage).observe(dt)

    t0 = time.perf_counter()
    spec = await build_filters(
        session, window, locations, k, use_geo=toggles.geo_filter, event_types=event_types
    )
    lap("filters", t0)

    t0 = time.perf_counter()
    vectors = await embedder.embed(queries)
    lap("embed_query", t0)

    lists: dict[str, list[int]] = {}
    matched_q: dict[int, str] = {}
    t0 = time.perf_counter()
    for i, (_q, vec) in enumerate(zip(queries, vectors, strict=True)):
        hits = await dense.dense_search(session, vec, spec, limit=30)
        lists[f"dense[{i}]"] = [cid for cid, _ in hits]
    if toggles.question_index:
        qhits = await dense.question_search(session, vectors[0], spec, limit=20)
        lists["question_index"] = [cid for cid, _, _ in qhits]
        matched_q = {cid: qq for cid, _, qq in qhits}
    lap("dense", t0)

    if toggles.hybrid:
        t0 = time.perf_counter()
        lists["fulltext"] = [
            cid for cid, _ in await sparse.fulltext_search(session, " ".join(queries), spec, 30)
        ]
        names = [m.mention for m in locations] + [m.name for m in locations]
        lists["trigram"] = [cid for cid, _ in await sparse.trigram_search(session, names, spec, 30)]
        lap("sparse", t0)

    scores, ranks = rrf(lists)
    fused = sorted(scores, key=lambda cid: -scores[cid])[: settings.rerank_candidates]

    t0 = time.perf_counter()
    rows = (
        (
            await session.execute(
                text(_FETCH_SQL),
                {
                    "ids": fused,
                    "q_geom": spec.query_geom.wkt if spec.query_geom is not None else None,
                },
            )
        ).all()
        if fused
        else []
    )
    by_id = {int(r.id): r for r in rows}
    lap("fetch", t0)

    candidates: list[Candidate] = []
    for cid in fused:
        r = by_id.get(cid)
        if r is None:
            continue
        candidates.append(
            Candidate(
                chunk_id=cid,
                document_id=r.document_id,
                title=r.title,
                url=r.url,
                source_id=r.source_id,
                trust_level=r.trust_level,
                publisher=r.publisher,
                authority=r.authority,
                language=r.language,
                published_at=r.published_at,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
                event_type=r.event_type,
                severity=r.severity,
                text=r.text,
                text_with_context=r.text_with_context,
                metadata=r.metadata or {},
                geom_geojson=r.geojson,
                distance_m=float(r.dist) if r.dist is not None else None,
                also_reported_by=int(r.dupes or 0),
                ranks=ranks.get(cid, {}),
                rrf=scores[cid],
                matched_question=matched_q.get(cid),
            )
        )

    t0 = time.perf_counter()
    rr = reranker if toggles.rerank else LexicalReranker()
    # score against the original and its rewrites (incl. the English one) and keep the
    # best per passage, so a Hindi question can still match an English advisory
    passages = [(c.chunk_id, c.text_with_context[:2000]) for c in candidates]
    rerank_scores: dict[int, float] = {}
    for q in list(dict.fromkeys(queries))[:3]:
        for cid, score in (await rerank(q, passages, rr)).items():
            rerank_scores[cid] = max(score, rerank_scores.get(cid, 0.0))
    if not toggles.rerank:
        # without a reranker, rank purely by fused RRF (normalised to [0,1])
        top = max((c.rrf for c in candidates), default=1.0) or 1.0
        rerank_scores = {
            c.chunk_id: c.rrf / top * 0.6 + rerank_scores.get(c.chunk_id, 0) * 0.4
            for c in candidates
        }
    lap("rerank", t0)

    ref = now
    if window is not None and not window.is_live:
        ref = window.start + (window.end - window.start) / 2
    for c in candidates:
        c.rerank = rerank_scores.get(c.chunk_id, 0.0)
        if window is not None and (c.valid_from or c.valid_to):
            # relevant for the *asked* window: no decay while validity overlaps it
            dt = freshness.window_gap_hours(window.start, window.end, c.valid_from, c.valid_to)
        else:
            dt = freshness.delta_hours(ref, c.valid_from, c.valid_to, c.published_at)
        c.freshness = freshness.freshness(intent, dt, enabled=toggles.freshness)
        c.trust = freshness.trust(c.trust_level)
        c.proximity = freshness.proximity(c.distance_m, spec.radius_m)
        c.final = c.rerank * c.freshness * c.trust * c.proximity

    candidates.sort(key=lambda c: -c.final)
    kept, dropped = candidates[:k], candidates[k:]
    dropped_trace = [
        {**c.trace(), "reason": "below top-k after final scoring"} for c in dropped[:20]
    ]
    trace: dict[str, Any] = {
        "filters": spec.trace,
        "lists": {name: ids[:15] for name, ids in lists.items()},
        "list_sizes": {name: len(ids) for name, ids in lists.items()},
        "fused": len(scores),
        "reranked": len(candidates),
        "reranker": rr.name,
        "embedder": embedder.name,
        "toggles": asdict(toggles),
        "timings_ms": timings,
        "candidates": [c.trace() for c in kept],
        "dropped": dropped_trace,
    }
    return RetrievalResult(kept, dropped_trace, trace)
