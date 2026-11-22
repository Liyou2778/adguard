"""Rerank stage.

M3 ships two implementations behind one interface:

* **FeatureReranker** - explainable weighted features (BM25, dense similarity, family
  dictionary hit, exact rule/article match, clause granularity). Weights live in
  `data/retrieval/rerank_weights.json` so tuning is data, not code.
* **NoopReranker** - keeps RRF order; used as an ablation in the evaluation report.

A cross-encoder reranker (bge-reranker / TEI) is the M5 upgrade; WeKnora exposes the
same pluggable rerank endpoint contract, so the swap is a config change later.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_WEIGHTS = Path(__file__).resolve().parents[3] / "data" / "retrieval" / "rerank_weights.json"


@dataclass(frozen=True)
class RerankFeatures:
    bm25: float = 0.0
    dense: float = 0.0
    family_hits: float = 0.0
    rule_exact: float = 0.0
    item_level: float = 0.0
    role_fit: float = 0.0


class Reranker(Protocol):
    name: str

    def rerank(self, features: dict[str, RerankFeatures]) -> list[tuple[str, float]]:
        ...


class NoopReranker:
    name = "noop"

    def rerank(self, features: dict[str, RerankFeatures], fused: dict[str, float] | None = None):
        if fused is None:
            raise ValueError("NoopReranker needs the fused scores")
        return sorted(fused.items(), key=lambda item: (-item[1], item[0]))


@dataclass
class FeatureReranker:
    """Weighted, per-feature min-max normalised reranker (explainable by design)."""

    weights: dict[str, float] | None = None
    name: str = "feature-rerank-v1"

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = load_weights()

    def rerank(self, features: dict[str, RerankFeatures]) -> list[tuple[str, float]]:
        if not features:
            return []
        normalised: dict[str, dict[str, float]] = {}
        for feature_name in ("bm25", "dense", "family_hits", "rule_exact", "item_level", "role_fit"):
            values = {clause_id: getattr(feature, feature_name) for clause_id, feature in features.items()}
            low = min(values.values())
            high = max(values.values())
            span = high - low
            normalised[feature_name] = {
                clause_id: (value - low) / span if span > 1e-12 else (1.0 if value > 0 else 0.0)
                for clause_id, value in values.items()
            }
        weights = self.weights or {}
        scored: list[tuple[str, float]] = []
        for clause_id in features:
            score = sum(
                weights.get(feature_name, 0.0) * normalised[feature_name][clause_id]
                for feature_name in normalised
            )
            scored.append((clause_id, round(score, 6)))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored

    def explain(self, features: dict[str, RerankFeatures]) -> list[dict]:
        """Per-candidate feature dump for debugging / evidence."""
        return [
            {"clause_id": clause_id, **asdict(feature)} for clause_id, feature in features.items()
        ]


def load_weights(path: Path | None = None) -> dict[str, float]:
    weights_path = Path(path or DEFAULT_WEIGHTS)
    if not weights_path.exists():
        return {"bm25": 0.15, "dense": 0.35, "family_hits": 0.20, "rule_exact": 0.10,
                "item_level": 0.05, "role_fit": 0.15}
    payload = json.loads(weights_path.read_text(encoding="utf-8"))
    return {key: float(value) for key, value in payload["weights"].items()}
