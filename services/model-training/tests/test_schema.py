from __future__ import annotations

import json

import pytest

from adguard_training.schema import OutputParseError, gold_output, parse_output

PASS_RAW = '{"decision":"pass","violations":[]}'
BLOCK_RAW = json.dumps(
    {
        "decision": "block",
        "violations": [
            {
                "rule_id": "ADV-LAW-ART9-3",
                "clause_id": "ADV-LAW-ART9-ITEM3",
                "article": "第九条",
                "item": "（三）",
                "span": [0, 4],
                "evidence": "全网最低",
                "penalty_law_ref": "第五十七条",
                "penalty_min": 200000,
                "penalty_max": 1000000,
            }
        ],
    },
    ensure_ascii=False,
)


def test_parse_pass():
    parsed = parse_output(PASS_RAW)
    assert parsed.decision == "pass"
    assert parsed.violations == ()


def test_parse_block_with_span():
    parsed = parse_output(BLOCK_RAW)
    assert parsed.decision == "block"
    violation = parsed.violations[0]
    assert violation.rule_id == "ADV-LAW-ART9-3"
    assert violation.clause_id == "ADV-LAW-ART9-ITEM3"
    assert violation.span == (0, 4)


def test_parse_json_inside_markdown_fence():
    parsed = parse_output("```json\n" + BLOCK_RAW + "\n```")
    assert parsed.decision == "block"


def test_reject_block_without_violations():
    with pytest.raises(OutputParseError):
        parse_output('{"decision":"block","violations":[]}')


def test_reject_pass_with_violations():
    with pytest.raises(OutputParseError):
        parse_output('{"decision":"pass","violations":[{"rule_id":"ADV-LAW-ART9-3","clause_id":"ADV-LAW-ART9-ITEM3"}]}')


def test_reject_unknown_rule():
    bad = json.loads(BLOCK_RAW)
    bad["violations"][0]["rule_id"] = "ADV-LAW-ART99"
    with pytest.raises(OutputParseError):
        parse_output(json.dumps(bad, ensure_ascii=False))


def test_reject_non_json():
    with pytest.raises(OutputParseError):
        parse_output("我认为这条广告违规")


def test_gold_output_roundtrip():
    assert parse_output(gold_output("pass", [])).decision == "pass"
