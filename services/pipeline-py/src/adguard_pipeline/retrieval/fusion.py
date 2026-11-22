"""Rank fusion: Reciprocal Rank Fusion (RRF).

RRF (k=60) is the standard route-merging baseline for hybrid retrieval: it needs no
score calibration between BM25 (unbounded) and cosine similarity ([-1,1]), which is
exactly why WeKnora-style hybrid search keeps per-route ranks rather than raw scores.
"""

from __future__ import annotations


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Fuse ranked id lists into one score map (higher is better)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))


def normalize(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalise scores into [0,1] for rerank feature blending."""
    if not scores:
        return {}
    values = list(scores.values())
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return {key: 1.0 for key in scores}
    return {key: (value - low) / (high - low) for key, value in scores.items()}
