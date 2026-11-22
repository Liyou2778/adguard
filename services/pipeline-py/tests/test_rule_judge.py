from __future__ import annotations

import pytest

from adguard_pipeline.retrieval.clause_source import JsonClauseSource, text_revision
from adguard_pipeline.retrieval.embeddings import HashingEmbedder
from adguard_pipeline.retrieval.rule_judge import JUDGE_VERSION, RuleJudge
from adguard_pipeline.retrieval.service import RetrievalService
from adguard_pipeline.retrieval.store import RetrievalStore


@pytest.fixture(scope="module")
def judge():
    store = RetrievalStore(":memory:", dim=512)
    service = RetrievalService(store, embedder=HashingEmbedder(512))
    service.build(JsonClauseSource().load())
    yield RuleJudge(service)
    store.close()


def test_absolute_terms_blocks_with_clause_and_penalty(judge):
    judgment = judge.judge("全网最低价，错过再等一年")
    assert judgment.decision == "block"
    assert judgment.judge_version == JUDGE_VERSION
    assert len(judgment.violations) == 1, "overlapping detections must merge into one violation"
    violation = judgment.violations[0]
    assert violation["rule_id"] == "ADV-LAW-ART9-3"
    assert violation["clause_id"] == "ADV-LAW-ART9-ITEM3"
    assert violation["article"] == "第九条"
    assert violation["item_label"] == "（三）"
    assert violation["penalty_min"] == 200_000
    assert violation["penalty_max"] == 1_000_000
    assert violation["penalty_law_ref"] == "第五十七条"
    assert len(violation["clause_revision"]) == 64
    assert 0.8 <= violation["confidence"] <= 1.0


def test_false_advertising_blocks_with_article_28_rule(judge):
    judgment = judge.judge("虚假广告：伪造检测报告，商品根本不存在")
    assert judgment.decision == "block"
    rule_ids = {violation["rule_id"] for violation in judgment.violations}
    assert rule_ids == {"ADV-LAW-ART28"}
    assert judgment.violations[0]["clause_id"].startswith("ADV-LAW-ART28")


def test_evasive_spelling_still_blocks(judge):
    judgment = judge.judge("本店最\u200b😀佳，全网最低价")
    assert judgment.decision == "block"
    assert any("最佳" in violation["evidence"] or violation["evidence"] for violation in judgment.violations)


def test_clean_copy_passes(judge):
    judgment = judge.judge("本店商品支持七天无理由退换。")
    assert judgment.decision == "pass"
    assert judgment.violations == []


def test_exemption_hint_routes_to_manual(judge):
    judgment = judge.judge("销量第一（有据可查的历史事实，可提供证明）")
    assert judgment.decision == "manual"
    assert judgment.violations == []
    assert "免责语境" in judgment.reason


def test_stale_index_never_cites_stale_revision(judge):
    service = judge._retrieval  # noqa: SLF001 - test introspection
    original = service.source_revisions()
    stale = dict(original)
    stale["ADV-LAW-ART9-ITEM3"] = text_revision("clause changed after index build")
    service.update_source_revisions(stale)
    try:
        judgment = judge.judge("全网最低价")
        cited = {violation["clause_revision"] for violation in judgment.violations}
        assert original["ADV-LAW-ART9-ITEM3"] not in cited
    finally:
        service.update_source_revisions(original)
