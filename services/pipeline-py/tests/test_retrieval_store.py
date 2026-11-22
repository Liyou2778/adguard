from __future__ import annotations

import numpy as np

from adguard_pipeline.retrieval.clause_source import text_revision
from adguard_pipeline.retrieval.models import ClauseRecord
from adguard_pipeline.retrieval.store import RetrievalStore


def record(clause_id: str, text: str, article_no: int = 9) -> ClauseRecord:
    return ClauseRecord(
        clause_id=clause_id,
        regulation_id="ADV-LAW",
        article=f"第{article_no}条",
        article_no=article_no,
        chapter=None,
        level="article",
        item_label=None,
        parent_clause_id=None,
        text=text,
        revision=text_revision(text),
    )


def test_upsert_search_and_delete():
    store = RetrievalStore(":memory:", dim=4)
    records = [record("a", "绝对化用语"), record("b", "虚假广告")]
    vectors = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
    assert store.upsert_clauses(records, vectors) == 2
    assert store.count() == 2

    hits = store.vector_search(np.array([1, 0, 0, 0], dtype=np.float32), top_k=2)
    assert hits[0][0] == "a"

    assert store.delete_clause("a") is True
    assert store.count() == 1
    assert store.delete_clause("missing") is False
    store.close()


def test_upsert_replaces_existing_clause():
    store = RetrievalStore(":memory:", dim=4)
    first = record("a", "旧条款")
    store.upsert_clauses([first], np.array([[1, 0, 0, 0]], dtype=np.float32))

    updated = record("a", "新条款")
    store.upsert_clauses([updated], np.array([[0, 1, 0, 0]], dtype=np.float32))
    assert store.count() == 1
    assert store.revisions()["a"] == updated.revision
    hits = store.vector_search(np.array([0, 1, 0, 0], dtype=np.float32), top_k=1)
    assert hits[0][0] == "a"
    store.close()


def test_meta_roundtrip():
    store = RetrievalStore(":memory:", dim=2)
    store.set_meta("index_version", "abc")
    assert store.get_meta("index_version") == "abc"
    assert store.get_meta("missing", "fallback") == "fallback"
    store.close()
