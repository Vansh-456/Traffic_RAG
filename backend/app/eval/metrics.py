"""Evaluation metrics (retrieval, generation, system)."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from app.ingestion.parsing.language import detect_query_language


def recall_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    if not gold:
        return 1.0
    return len(set(retrieved[:k]) & gold) / len(gold)


def mrr(retrieved: Sequence[str], gold: set[str]) -> float:
    for i, r in enumerate(retrieved, start=1):
        if r in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], gold: set[str], k: int = 10) -> float:
    seen: set[str] = set()
    dcg = 0.0
    for i, r in enumerate(retrieved[:k], start=1):
        if r in gold and r not in seen:
            dcg += 1.0 / math.log2(i + 1)
            seen.add(r)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 1.0


def context_precision(retrieved: Sequence[str], gold: set[str], k: int = 8) -> float:
    top = list(retrieved[:k])
    if not top:
        return 0.0 if gold else 1.0
    return sum(1 for r in top if r in gold) / len(top)


def language_match(answer: str, expected: str) -> bool:
    detected = detect_query_language(answer)
    if expected == "hi":
        return detected == "hi"
    # Hinglish questions are answered in simple English (spec 6.5)
    return detected in ("en", "hinglish")


def citation_accuracy(
    cited_docs: Sequence[str], gold: set[str], supported_flags: Sequence[bool]
) -> float:
    """Share of citations that (a) point to a gold doc or (b) were judged supporting."""
    if not cited_docs and not supported_flags:
        return 1.0
    flags = list(supported_flags) or [d in gold for d in cited_docs]
    return sum(flags) / len(flags) if flags else 1.0


def answer_relevancy(question: str, answer: str) -> float:
    """Token-overlap proxy for RAGAS answer relevancy (no LLM needed)."""
    import re

    qw = {w for w in re.findall(r"[\wऀ-ॿ]+", question.lower()) if len(w) > 3}
    aw = set(re.findall(r"[\wऀ-ॿ]+", answer.lower()))
    return len(qw & aw) / len(qw) if qw else 1.0


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    idx = min(len(vs) - 1, max(0, math.ceil(p / 100 * len(vs)) - 1))
    return float(vs[idx])


def mean(values: Sequence[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0
