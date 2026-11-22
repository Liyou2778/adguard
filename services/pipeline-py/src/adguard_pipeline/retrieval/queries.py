"""Retrieval evaluation queries.

Three splits:
    material  hand-written marketing-copy questions (held-out test set)
    penalty   hand-written questions about penalties (reported, not gated)
    clause    generated from clause text via distinctive bigrams (sanity / dev split
              for weight tuning; it is lexical by construction, so it is not the
              headline metric)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .models import ClauseRecord
from .service import is_penalty_clause

DEFAULT_QUERIES = Path(__file__).resolve().parents[3] / "data" / "retrieval" / "retrieval_queries.jsonl"

_GENERIC_BIGRAMS = {
    "广告", "不得", "使用", "下列", "情形", "规定", "以下", "以上", "应当", "或者",
    "以及", "其他", "商品", "服务", "发布", "违反", "本法", "法律", "行政", "法规",
    "含有", "内容", "进行", "可以", "有关", "机关", "管理", "市场", "监督",
}


@dataclass(frozen=True)
class EvalQuery:
    id: str
    split: str
    query: str
    gold_ids: frozenset[str]
    note: str = ""


def load_handwritten(path: Path | None = None) -> list[dict]:
    source = Path(path or DEFAULT_QUERIES)
    rows = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def acceptable_ids(gold: str, clauses: dict[str, ClauseRecord]) -> frozenset[str]:
    """A hit on the parent article counts at article granularity; the item is finer."""
    ids = {gold}
    record = clauses.get(gold)
    if record is not None and record.parent_clause_id:
        ids.add(record.parent_clause_id)
    return frozenset(ids)


def build_eval_queries(clauses: list[ClauseRecord]) -> list[EvalQuery]:
    by_id = {record.clause_id: record for record in clauses}
    queries: list[EvalQuery] = []
    for row in load_handwritten():
        queries.append(
            EvalQuery(
                id=row["id"],
                split=row["split"],
                query=row["query"],
                gold_ids=acceptable_ids(row["gold"], by_id),
                note=row.get("note", ""),
            )
        )
    queries.extend(_generate_clause_queries(clauses))
    return queries


def _bigrams(text: str) -> list[str]:
    cleaned = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    return [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]


def _distinctive_bigrams(clauses: list[ClauseRecord]) -> dict[str, list[str]]:
    """Rank each clause's bigrams by inverse document frequency (no sklearn needed)."""
    documents = {record.clause_id: _bigrams(record.text) for record in clauses}
    df: dict[str, int] = {}
    for grams in documents.values():
        for gram in set(grams):
            df[gram] = df.get(gram, 0) + 1
    total = len(documents)
    result: dict[str, list[str]] = {}
    for clause_id, grams in documents.items():
        counts: dict[str, int] = {}
        for gram in grams:
            if gram in _GENERIC_BIGRAMS:
                continue
            counts[gram] = counts.get(gram, 0) + 1
        scored = sorted(
            counts.items(),
            key=lambda item: (
                -(item[1] * math.log((total + 1) / (df.get(item[0], 1) + 0.5))),
                item[0],
            ),
        )
        result[clause_id] = [gram for gram, _ in scored[:8]]
    return result


def _generate_clause_queries(clauses: list[ClauseRecord]) -> list[EvalQuery]:
    distinctive = _distinctive_bigrams(clauses)
    by_id = {record.clause_id: record for record in clauses}
    generated: list[EvalQuery] = []
    for record in clauses:
        grams = distinctive.get(record.clause_id, [])
        if len(grams) < 4:
            continue
        # Penalty clauses need a penalty-intent query, otherwise the role filter
        # correctly treats the query as a content question and excludes them.
        penalty = is_penalty_clause(record)
        if penalty:
            query_a = "、".join(grams[:3]) + "的罚款规定是什么"
            query_b = "涉及" + "、".join(grams[4:8] or grams[:4]) + "怎么处罚"
        else:
            query_a = "关于" + "、".join(grams[:4]) + "的广告规定"
            query_b = "哪些情况涉及" + "、".join(grams[4:8] or grams[:4])
        generated.append(
            EvalQuery(
                id=f"clause-{record.clause_id}-a",
                split="clause",
                query=query_a,
                gold_ids=acceptable_ids(record.clause_id, by_id),
                note="generated from top bigrams",
            )
        )
        generated.append(
            EvalQuery(
                id=f"clause-{record.clause_id}-b",
                split="clause",
                query=query_b,
                gold_ids=acceptable_ids(record.clause_id, by_id),
                note="generated from secondary bigrams",
            )
        )
    return generated
