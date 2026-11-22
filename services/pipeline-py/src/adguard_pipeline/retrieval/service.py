"""Clause retrieval service: multi-route index + RRF fusion + rerank + revision guard.

Routes (PRD FR-4):
    1. sparse  - character-bigram BM25 over normalized clause text;
    2. dense   - bge-small-zh-v1.5 embeddings in sqlite-vec KNN;
    3. rule    - exact rule id / 第X条 / （X）匹配（规则树/家族词典命中）;
    4. family  - M2 matcher families present in query ∩ clause families (rerank feature).

Correctness contract (PRD M3 / WeKnora chunk-revision invariant): a hit whose
clause revision is no longer current must be dropped, not returned.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ..clause_parser import chinese_number_to_int
from ..matcher import Matcher
from ..normalization import Normalizer
from .bm25 import CharBigramBM25
from .clause_source import ClauseSource
from .embeddings import Embedder, resolve_embedder
from .fusion import normalize as normalize_scores
from .fusion import rrf
from .models import ClauseRecord, RetrievalHit, RetrievalResult
from .rerank import FeatureReranker, RerankFeatures, Reranker
from .store import RetrievalStore

logger = logging.getLogger(__name__)

ARTICLE_RE = re.compile(r"第([零〇一二三四五六七八九十百千两\d]+)条")
ITEM_RE = re.compile(r"（([一二三四五六七八九十]+)）")
PENALTY_QUERY_RE = re.compile(r"罚|处罚|罚款|责任|吊销|多少|金额|区间")
_PENALTY_ARTICLES = {55, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66}


def is_penalty_clause(record: ClauseRecord) -> bool:
    """Sanction/sanction-procedure clause vs substantive prohibition clause."""
    return record.article_no in _PENALTY_ARTICLES or "罚款" in record.text


@dataclass
class IndexStatus:
    clauses: int
    index_version: str
    embedder: str
    dimensions: int
    updated_at: str


class RetrievalService:
    def __init__(
        self,
        store: RetrievalStore,
        embedder: Embedder | None = None,
        matcher: Matcher | None = None,
        normalizer: Normalizer | None = None,
        reranker: Reranker | None = None,
        source_revisions: dict[str, str] | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder or resolve_embedder()
        self._matcher = matcher or Matcher()
        self._normalizer = normalizer or Normalizer.from_data()
        self._reranker = reranker or FeatureReranker()
        self._bm25 = CharBigramBM25()
        self._source_revisions: dict[str, str] = dict(source_revisions or {})

    # ------------------------------------------------------------------ index
    def build(self, records: list[ClauseRecord]) -> IndexStatus:
        enriched = [self._enrich(record) for record in records]
        if enriched:
            vectors = self._embedder.embed([self._normalizer.normalize(r.text).text for r in enriched])
            self._store.upsert_clauses(enriched, vectors)
        self._bm25.fit({record.clause_id: self._normalizer.normalize(record.text).text
                        for record in enriched})
        self._source_revisions = {record.clause_id: record.revision for record in enriched}
        version = self._compute_index_version(enriched)
        self._store.set_meta("index_version", version)
        self._store.set_meta("embedder", self._embedder.name)
        self._store.set_meta("updated_at", datetime.now(timezone.utc).isoformat())
        logger.info("retrieval index built: %d clauses, version=%s, embedder=%s",
                    len(enriched), version[:12], self._embedder.name)
        return self.status()

    def rebuild(self, source: ClauseSource) -> IndexStatus:
        return self.build(source.load())

    def upsert_clause(self, record: ClauseRecord) -> IndexStatus:
        """Incremental update of one clause (rule hot-update path)."""
        enriched = self._enrich(record)
        vector = self._embedder.embed([self._normalizer.normalize(enriched.text).text])
        self._store.upsert_clauses([enriched], vector)
        self._bm25.fit({item.clause_id: self._normalizer.normalize(item.text).text
                        for item in self._store.all_clauses()})
        self._source_revisions[record.clause_id] = record.revision
        version = self._compute_index_version(self._store.all_clauses())
        self._store.set_meta("index_version", version)
        return self.status()

    def delete_clause(self, clause_id: str) -> IndexStatus:
        self._store.delete_clause(clause_id)
        self._source_revisions.pop(clause_id, None)
        self._bm25.fit({item.clause_id: self._normalizer.normalize(item.text).text
                        for item in self._store.all_clauses()})
        version = self._compute_index_version(self._store.all_clauses())
        self._store.set_meta("index_version", version)
        return self.status()

    def update_source_revisions(self, revisions: dict[str, str]) -> None:
        """Simulate/record the source-of-truth revision map (e.g. after a DB change)."""
        self._source_revisions = dict(revisions)

    def source_revisions(self) -> dict[str, str]:
        return dict(self._source_revisions)

    # ------------------------------------------------------------------ search
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        use_sparse: bool = True,
        use_dense: bool = True,
        use_rerank: bool = True,
        reranker: Reranker | None = None,
        family_hint: str | None = None,
    ) -> RetrievalResult:
        normalized_query = self._normalizer.normalize(query).text
        all_clauses = {record.clause_id: record for record in self._store.all_clauses()}
        penalty_query = bool(PENALTY_QUERY_RE.search(query))
        # Role filter: a content question is answered by prohibition clauses, a
        # penalty question by sanction clauses. Mixing them up is the #1 way to
        # return a legally wrong citation, so the filter is explicit (M3 rule intent;
        # M4 replaces it with model intent classification).
        eligible = {
            clause_id
            for clause_id, record in all_clauses.items()
            if self._is_penalty_clause(record) == penalty_query
        }
        sparse = [
            (clause_id, score)
            for clause_id, score in (self._bm25.search(normalized_query, top_k=30) if use_sparse else [])
            if clause_id in eligible
        ]
        dense = []
        if use_dense:
            dense = [
                (clause_id, distance)
                for clause_id, distance in self._store.vector_search(
                    self._embedder.embed([normalized_query])[0], top_k=30
                )
                if clause_id in eligible
            ]

        clauses = {clause_id: all_clauses[clause_id] for clause_id in eligible}
        query_families = self._query_families(query)
        query_article, query_item = self._query_article_item(query)

        rankings = [[clause_id for clause_id, _ in sparse]]
        if dense:
            rankings.append([clause_id for clause_id, _ in dense])
        # Rule route: clauses whose *role* matches the query intent rank first.
        rankings.append(self._role_ranking(penalty_query, dict(sparse), clauses))
        if family_hint:
            # Family-dictionary route: when the M2 matcher already told us which family
            # fired, all clauses tagged with that family are legitimate candidates even
            # when the wording does not literally overlap (e.g. "销量第一" -> 第九条(三)).
            rankings.append(self._family_ranking(family_hint, dict(sparse), clauses))
        fused = rrf(rankings)

        candidates = list(fused)[:20]
        features: dict[str, RerankFeatures] = {}
        for clause_id in candidates:
            record = clauses.get(clause_id)
            if record is None:
                continue
            is_penalty_clause = self._is_penalty_clause(record)
            features[clause_id] = RerankFeatures(
                bm25=dict(sparse).get(clause_id, 0.0),
                dense=self._cosine_from_distance(dict(dense).get(clause_id)) if dense else 0.0,
                family_hits=float(len(set(query_families) & set(record.families))),
                rule_exact=1.0 if self._rule_match(record, query, query_article, query_item) else 0.0,
                item_level=1.0 if record.level == "item" else 0.0,
                role_fit=self._role_fit(is_penalty_clause, penalty_query),
            )

        active_reranker = reranker or self._reranker
        if use_rerank and features:
            if active_reranker.name == "noop":
                ordered = active_reranker.rerank(features, fused)  # type: ignore[arg-type]
            else:
                ordered = active_reranker.rerank(features)
        else:
            ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))

        bm25_map = dict(sparse)
        dense_map = {clause_id: self._cosine_from_distance(distance) for clause_id, distance in dense}
        hits: list[RetrievalHit] = []
        dropped_stale = 0
        for clause_id, rerank_score in ordered:
            record = clauses.get(clause_id)
            if record is None:
                continue
            current_revision = self._source_revisions.get(record.clause_id, record.revision)
            if current_revision != record.revision:
                # Chunk changed since indexing: the stale hit must never be returned.
                dropped_stale += 1
                continue
            feature = features.get(clause_id, RerankFeatures())
            hits.append(
                RetrievalHit(
                    clause_id=record.clause_id,
                    revision=record.revision,
                    score=float(rerank_score),
                    bm25_score=float(bm25_map.get(clause_id, 0.0)),
                    dense_score=float(dense_map.get(clause_id, 0.0)),
                    rerank_score=float(rerank_score),
                    family_hits=int(feature.family_hits),
                    text=record.text,
                    article=record.article,
                    item_label=record.item_label,
                    regulation_id=record.regulation_id,
                    law=record.law,
                    families=record.families,
                )
            )
            if len(hits) >= top_k:
                break

        return RetrievalResult(
            query=query,
            hits=hits,
            dropped_stale=dropped_stale,
            index_version=self._store.get_meta("index_version"),
            warnings=[] if dropped_stale == 0 else [f"{dropped_stale} stale hit(s) dropped (revision mismatch)"],
        )

    # ------------------------------------------------------------------ helpers
    def _enrich(self, record: ClauseRecord) -> ClauseRecord:
        families = tuple(sorted(self._matcher.match(record.text).families()))
        return replace(record, families=families)

    def _compute_index_version(self, records: list[ClauseRecord]) -> str:
        payload = json.dumps(
            [[record.clause_id, record.revision] for record in sorted(records, key=lambda r: r.clause_id)]
            + [["embedder", self._embedder.name]],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _query_families(self, query: str) -> set[str]:
        return self._matcher.match(query).families()

    @staticmethod
    def _cosine_from_distance(distance: float | None) -> float:
        """Normalized vectors: L2^2 = 2 - 2*cos => cos = 1 - d^2/2 (clamped)."""
        if distance is None:
            return 0.0
        return max(0.0, min(1.0, 1.0 - (distance * distance) / 2.0))

    def _role_ranking(self, penalty_query: bool, sparse_scores: dict[str, float],
                      clauses: dict[str, ClauseRecord]) -> list[str]:
        """Rank clauses by role-intent fit, then by BM25 (rule-tree route for M3)."""
        def sort_key(clause_id: str) -> tuple[int, float, str]:
            record = clauses[clause_id]
            role_match = 0 if self._is_penalty_clause(record) == penalty_query else 1
            return role_match, -sparse_scores.get(clause_id, 0.0), clause_id

        return sorted(clauses, key=sort_key)

    @staticmethod
    def _family_ranking(family: str, sparse_scores: dict[str, float],
                        clauses: dict[str, ClauseRecord]) -> list[str]:
        def sort_key(clause_id: str) -> tuple[int, float, str]:
            record = clauses[clause_id]
            own = 0 if family in record.families else 1
            return own, -sparse_scores.get(clause_id, 0.0), clause_id

        return sorted(clauses, key=sort_key)

    @staticmethod
    def _is_penalty_clause(record: ClauseRecord) -> bool:
        return is_penalty_clause(record)

    @staticmethod
    def _role_fit(is_penalty_clause: bool, penalty_query: bool) -> float:
        """+1 when clause role matches query intent; -1 when it clearly does not.

        This is the rule-based "intent" part of M3 reranking: "用绝对化用语怎么罚" wants
        第五十七条, while "能不能写销量第一" wants 第九条（三）. It is explainable and
        testable; M4 replaces it with the model's intent understanding.
        """
        if is_penalty_clause and penalty_query:
            return 1.0
        if is_penalty_clause and not penalty_query:
            return -1.0
        if not is_penalty_clause and penalty_query:
            return -0.5
        return 0.0

    @staticmethod
    def _query_article_item(query: str) -> tuple[int | None, int | None]:
        article = None
        item = None
        match = ARTICLE_RE.search(query)
        if match:
            try:
                article = chinese_number_to_int(match.group(1))
            except ValueError:
                article = None
        item_match = ITEM_RE.search(query)
        if item_match:
            try:
                item = chinese_number_to_int(item_match.group(1))
            except ValueError:
                item = None
        return article, item

    @staticmethod
    def _rule_match(record: ClauseRecord, query: str, query_article: int | None,
                    query_item: int | None) -> bool:
        lowered = query.lower()
        if record.clause_id.lower() in lowered:
            return True
        if query_article is not None and record.article_no == query_article:
            if query_item is None:
                return True
            return record.level == "item" and record.item_label is not None and record.item_label == f"（{query_item}）"
        return False

    # ------------------------------------------------------------------ status
    def status(self) -> IndexStatus:
        return IndexStatus(
            clauses=self._store.count(),
            index_version=self._store.get_meta("index_version"),
            embedder=self._store.get_meta("embedder", self._embedder.name),
            dimensions=self._embedder.dim,
            updated_at=self._store.get_meta("updated_at"),
        )

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    def set_reranker(self, reranker: Reranker) -> None:
        self._reranker = reranker

    @property
    def store(self) -> RetrievalStore:
        return self._store
