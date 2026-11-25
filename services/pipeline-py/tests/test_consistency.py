"""Unit tests for FR-6 cross-modal consistency (rules + mocked LLM)."""

from __future__ import annotations

from adguard_pipeline import consistency


def _rule_conflicts(caption: str, image_text: str) -> list[dict]:
    result = consistency.check_consistency(caption, image_text, use_llm=False)
    return [conflict.to_dict() for conflict in result.conflicts]


def test_price_conflict_detected():
    conflicts = _rule_conflicts("到手价 99 元", "活动价 199 元")
    assert len(conflicts) == 1
    assert conflicts[0]["attribute"] == "price"
    assert conflicts[0]["caption_value"] == "99"
    assert conflicts[0]["image_value"] == "199"


def test_price_same_value_is_not_a_conflict():
    assert _rule_conflicts("到手价 99 元", "活动价 99 元") == []


def test_discount_conflict_detected():
    conflicts = _rule_conflicts("全场 5 折", "全场 8 折")
    assert len(conflicts) == 1
    assert conflicts[0]["attribute"] == "discount"


def test_gift_buy_one_get_one_vs_half_price_conflict():
    conflicts = _rule_conflicts("买一送一", "第二件半价")
    assert len(conflicts) == 1
    assert conflicts[0]["attribute"] == "gift"
    assert "买" in conflicts[0]["evidence_caption"]
    assert "半价" in conflicts[0]["evidence_image"]


def test_spec_conflict_detected():
    conflicts = _rule_conflicts("500ml 大瓶装", "750ml 家庭装")
    assert len(conflicts) == 1
    assert conflicts[0]["attribute"] == "spec"


def test_validity_conflict_detected():
    conflicts = _rule_conflicts("活动 7 天内有效", "仅限 30 天")
    assert len(conflicts) == 1
    assert conflicts[0]["attribute"] == "validity"


def test_no_claims_no_conflict_and_skipped_when_empty():
    assert _rule_conflicts("品质好物", "欢迎选购") == []
    result = consistency.check_consistency("", "活动价 99 元", use_llm=False)
    assert result.conflicts == []
    assert any("skipped" in warning for warning in result.warnings)
    result = consistency.check_consistency("活动价 99 元", "", use_llm=False)
    assert result.conflicts == []


def test_bbox_mapped_from_lines():
    lines = [
        {"text": "活动价 199 元", "bbox": [10, 20, 200, 60]},
        {"text": "限时抢购", "bbox": [10, 80, 300, 120]},
    ]
    result = consistency.check_consistency("到手价 99 元", "活动价 199 元\n限时抢购", lines,
                                           use_llm=False)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].bbox == [10, 20, 200, 60]


def test_llm_gray_zone_used_and_validated(monkeypatch):
    calls = {"n": 0}

    def fake_llm(caption: str, image_text: str):
        calls["n"] += 1
        return [{
            "attribute": "other", "caption_value": "全网最低", "image_value": "销量第一",
            "evidence_caption": "全网最低", "evidence_image": "销量第一", "confidence": 0.7,
        }], None

    monkeypatch.setattr(consistency, "_llm_conflicts", fake_llm)
    result = consistency.check_consistency("全网最低 限时特惠", "销量第一 活动价 99 元", use_llm=True)
    assert calls["n"] == 1
    assert result.llm_used is True
    assert result.engine == "rules+llm"
    assert len(result.conflicts) == 1
    assert result.conflicts[0].source == "llm"
    assert result.conflicts[0].span_image == (0, 4)


def test_llm_failure_degrades_but_returns_rules(monkeypatch):
    def broken(caption: str, image_text: str):
        return [], "llm unavailable: timeout"

    monkeypatch.setattr(consistency, "_llm_conflicts", broken)
    result = consistency.check_consistency("全网最低 限时特惠", "销量第一 活动价 99 元", use_llm=True)
    assert result.degraded is True
    assert any("llm unavailable" in warning for warning in result.warnings)


def test_llm_discovers_semantic_conflict_between_promises(monkeypatch):
    # "买一赠二" vs "满 200 减 20" 不同规则但互斥的低价承诺 -> 规则不报，由 LLM 兜底
    def fake_llm(caption: str, image_text: str):
        return [{
            "attribute": "other", "caption_value": "买一赠二", "image_value": "满200减20",
            "evidence_caption": "买一赠二", "evidence_image": "满200减20", "confidence": 0.6,
        }], None

    monkeypatch.setattr(consistency, "_llm_conflicts", fake_llm)
    result = consistency.check_consistency("买一赠二 超值优惠", "满 200 减 20 超值优惠", use_llm=True)
    assert result.llm_used
    assert len(result.conflicts) == 1


def test_llm_skipped_when_rule_found_or_empty(monkeypatch):
    called = {"n": 0}

    def fake_llm(caption: str, image_text: str):
        called["n"] += 1
        return [], None

    monkeypatch.setattr(consistency, "_llm_conflicts", fake_llm)
    # rule already found a conflict -> no LLM call
    consistency.check_consistency("到手价 99 元 超值", "活动价 199 元 超值", use_llm=True)
    assert called["n"] == 0
