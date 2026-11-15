"""Reciprocal Rank Fusion across ranked lists."""

from __future__ import annotations

from collections import defaultdict

RRF_K = 60


def rrf(lists: dict[str, list[int]], k: int = RRF_K) -> tuple[dict[int, float], dict[int, dict[str, int]]]:
    """Fuse ranked id lists.

    Returns (scores, ranks) where ``ranks[id][list_name]`` is the 1-based rank
    of the id in that list (for the internals panel).
    """
    scores: dict[int, float] = defaultdict(float)
    ranks: dict[int, dict[str, int]] = defaultdict(dict)
    for name, ids in lists.items():
        seen: set[int] = set()
        for rank, cid in enumerate(ids, start=1):
            if cid in seen:
                continue
            seen.add(cid)
            scores[cid] += 1.0 / (k + rank)
            ranks[cid][name] = rank
    return dict(scores), dict(ranks)
