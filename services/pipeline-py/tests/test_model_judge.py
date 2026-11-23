from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from adguard_pipeline.retrieval.clause_source import JsonClauseSource
from adguard_pipeline.retrieval.embeddings import HashingEmbedder
from adguard_pipeline.retrieval.model_judge import (MODEL_JUDGE_VERSION, ModelJudgeClient,
                                                    ModelJudgeError, ModelVerdict, _parse_verdict,
                                                    load_rules_by_clause, validate_verdict)
from adguard_pipeline.retrieval.rule_judge import RuleJudge
from adguard_pipeline.retrieval.service import RetrievalService
from adguard_pipeline.retrieval.store import RetrievalStore

BLOCK_JSON = json.dumps(
    {"decision": "block", "violations": [{"rule_id": "ADV-LAW-ART9-3",
                                          "clause_id": "ADV-LAW-ART9-ITEM3",
                                          "span": [0, 4], "evidence": "最低价"}]},
    ensure_ascii=False,
)
PASS_JSON = json.dumps({"decision": "pass", "violations": []}, ensure_ascii=False)


class FakeResponse:
    def __init__(self, content: str, status: int = 200) -> None:
        self._content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture(scope="module")
def judge():
    store = RetrievalStore(":memory:", dim=512)
    service = RetrievalService(store, embedder=HashingEmbedder(512))
    service.build(JsonClauseSource().load())
    yield service
    store.close()


def make_judge(service, monkeypatch, content: str | None = None, error: Exception | None = None):
    if error is not None:
        def fake_post(*args, **kwargs):
            raise error
    else:
        def fake_post(*args, **kwargs):
            return FakeResponse(content or PASS_JSON)
    monkeypatch.setattr(httpx, "post", fake_post)
    client = ModelJudgeClient("http://127.0.0.1:8100")
    return RuleJudge(service, model_client=client)


def test_hybrid_uses_rule_block_when_model_disagrees(judge, monkeypatch):
    hybrid = make_judge(judge, monkeypatch, PASS_JSON)
    judgment = hybrid.judge("全网最低价，错过再等一年")
    assert judgment.decision == "block"
    assert judgment.judge_version == MODEL_JUDGE_VERSION


def test_hybrid_escalates_model_block_on_rule_pass(judge, monkeypatch):
    hybrid = make_judge(judge, monkeypatch, BLOCK_JSON)
    judgment = hybrid.judge("本店商品支持七天无理由退换")
    assert judgment.decision == "manual", "model cannot auto-block without rule support"
    assert any(v["source"] == "model" for v in judgment.violations)
    assert judgment.violations[0]["penalty_min"] == 200_000  # penalty from rule data, not model


def test_hybrid_accepts_model_pass_for_exemption_hint(judge, monkeypatch):
    hybrid = make_judge(judge, monkeypatch, PASS_JSON)
    judgment = hybrid.judge("销量第一（有据可查的历史数据，可提供证明）")
    assert judgment.decision == "pass"
    assert "模型确认" in judgment.reason


def test_hybrid_rejects_invalid_citation(judge, monkeypatch):
    bad = json.dumps({"decision": "block", "violations": [
        {"rule_id": "ADV-LAW-ART99", "clause_id": "ADV-LAW-ART99", "span": [0, 2], "evidence": "xx"}]},
        ensure_ascii=False)
    hybrid = make_judge(judge, monkeypatch, bad)
    judgment = hybrid.judge("本店商品支持七天无理由退换")
    # invalid model verdict -> fall back to the rule result (pass)
    assert judgment.decision == "pass"


def test_hybrid_degrades_when_model_unavailable(judge, monkeypatch):
    hybrid = make_judge(judge, monkeypatch, error=httpx.ConnectError("down"))
    judgment = hybrid.judge("全网最低价")
    assert judgment.decision == "block"
    assert "模型判定不可用" in judgment.reason


def test_parse_rejects_block_without_violations():
    with pytest.raises(ModelJudgeError):
        _parse_verdict(json.dumps({"decision": "block", "violations": []}))


def test_validate_verdict_rewrites_penalties_from_catalogue():
    rules = load_rules_by_clause(Path(__file__).resolve().parents[1]
                                 / "data" / "parsers" / "parsed_m2.json")
    verdict = ModelVerdict(decision="block", violations=[{
        "rule_id": "ADV-LAW-ART9-3", "clause_id": "ADV-LAW-ART9-ITEM3",
        "span": [0, 3], "evidence": "最低价", "penalty_min": 1, "penalty_max": 2}])
    validated = validate_verdict(verdict, rules)
    assert validated.violations[0]["penalty_min"] == 200_000
    assert validated.violations[0]["penalty_max"] == 1_000_000
