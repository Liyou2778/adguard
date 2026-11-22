from __future__ import annotations

import copy

import pytest

from adguard_pipeline.retrieval.clause_source import JsonClauseSource, text_revision
from adguard_pipeline.retrieval.embeddings import HashingEmbedder
from adguard_pipeline.retrieval.models import ClauseRecord
from adguard_pipeline.retrieval.service import RetrievalService
from adguard_pipeline.retrieval.store import RetrievalStore


@pytest.fixture(scope="module")
def records():
    return JsonClauseSource().load()


@pytest.fixture(scope="module")
def service(records):
    store = RetrievalStore(":memory:", dim=512)
    svc = RetrievalService(store, embedder=HashingEmbedder(512))
    svc.build(records)
    yield svc
    store.close()


def test_index_contains_all_clauses(service, records):
    status = service.status()
    assert status.clauses == len(records) == 33
    assert len(status.index_version) == 64
    assert status.dimensions == 512


def test_absolute_terms_query_hits_item3(service):
    result = service.retrieve("全网最低价的国家级产品", top_k=5)
    clause_ids = [hit.clause_id for hit in result.hits]
    assert "ADV-LAW-ART9-ITEM3" in clause_ids
    # penalty article must not outrank the prohibition clause for a content query
    assert result.hits[0].clause_id != "ADV-LAW-ART57"


def test_penalty_intent_prefers_penalty_article(service):
    result = service.retrieve("使用绝对化用语会被罚款多少", top_k=5)
    assert result.hits, "expected hits"
    # Intent filter: only sanction clauses are eligible for a penalty question.
    assert all(hit.clause_id in {"ADV-LAW-ART57", "ADV-LAW-ART55"} or
               hit.clause_id.startswith("ADV-LAW-ART57-ITEM") for hit in result.hits)
    assert any(hit.clause_id == "ADV-LAW-ART57" for hit in result.hits)


def test_content_query_excludes_penalty_clauses(service):
    result = service.retrieve("国家级认证", top_k=5)
    assert result.hits
    assert all(not hit.clause_id.startswith("ADV-LAW-ART55") and
               not hit.clause_id.startswith("ADV-LAW-ART57") for hit in result.hits)


def test_hits_carry_revision_and_clause_text(service):
    result = service.retrieve("国家级认证", top_k=3)
    for hit in result.hits:
        assert len(hit.revision) == 64
        assert hit.text
        assert hit.article.startswith("第")


def test_stale_revision_is_dropped(service):
    # Simulate: source-of-truth clause changed after indexing (index is stale).
    result_before = service.retrieve("国家级认证", top_k=5)
    target = result_before.hits[0].clause_id
    original = service.source_revisions()
    stale = dict(original)
    stale[target] = text_revision("changed content")
    service.update_source_revisions(stale)

    result_after = service.retrieve("国家级认证", top_k=5)
    assert all(hit.clause_id != target for hit in result_after.hits)
    assert result_after.dropped_stale >= 1
    assert result_after.warnings

    # Restore so later tests are unaffected.
    service.update_source_revisions(original)


def test_incremental_upsert_updates_index(service):
    record = ClauseRecord(
        clause_id="ADV-LAW-ART9-ITEM3",
        regulation_id="ADV-LAW",
        article="第九条",
        article_no=9,
        chapter=None,
        level="item",
        item_label="（三）",
        parent_clause_id="ADV-LAW-ART9",
        text="使用“国家级”、“最高级”、“最佳”、“全网最低”等用语；",
        revision=text_revision("使用“国家级”、“最高级”、“最佳”、“全网最低”等用语；"),
    )
    status = service.upsert_clause(record)
    assert status.clauses == 33
    result = service.retrieve("全网最低", top_k=3)
    assert any("全网最低" in hit.text for hit in result.hits)


def test_dense_disabled_still_works(service):
    result = service.retrieve("国家级认证", top_k=3, use_dense=False, use_rerank=False)
    assert result.hits
    assert all(hit.dense_score == 0.0 for hit in result.hits)


def test_retrieval_is_deterministic(service):
    first = [hit.clause_id for hit in service.retrieve("虚假广告判定", top_k=5).hits]
    second = [hit.clause_id for hit in service.retrieve("虚假广告判定", top_k=5).hits]
    assert first == second
