from __future__ import annotations

import pytest

from adguard_pipeline.clause_parser import ClauseParser, chinese_number_to_int


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("九", 9),
        ("十", 10),
        ("十一", 11),
        ("二十", 20),
        ("二十八", 28),
        ("五十七", 57),
        ("一百", 100),
        ("一百零五", 105),
        ("三百二十一", 321),
        ("1", 1),
        ("57", 57),
    ],
)
def test_chinese_number_to_int(text, expected):
    assert chinese_number_to_int(text) == expected


@pytest.fixture(scope="module")
def parsed():
    parser = ClauseParser()
    return parser.parse_all()


def test_articles_are_segmented_without_reference_splits(parsed):
    docs, _ = parsed
    law = docs["ADV-LAW"]
    assert [clause.article_no for clause in law.clauses] == [1, 4, 8, 9, 10, 11, 13, 28, 55, 57, 75]


def test_items_are_segmented(parsed):
    docs, _ = parsed
    law = docs["ADV-LAW"]
    article_9 = law.clause(9)
    assert article_9 is not None
    assert len(article_9.items) == 11
    assert article_9.items[2].item == "（三）"
    assert "国家级" in article_9.items[2].text


def test_cross_reference_stays_inside_penalty_item(parsed):
    docs, _ = parsed
    article_57 = docs["ADV-LAW"].clause(57)
    assert article_57 is not None
    assert len(article_57.items) == 6
    assert "第九条" in article_57.items[0].text


def test_rules_are_built_with_penalty_and_terms(parsed):
    _, rules = parsed
    all_rules = {rule.rule_id: rule for doc_rules in rules.values() for rule in doc_rules}
    absolute = all_rules["ADV-LAW-ART9-3"]
    assert absolute.family == "absolute_terms"
    assert absolute.article == "第九条"
    assert absolute.item == "（三）"
    assert absolute.clause_id == "ADV-LAW-ART9-ITEM3"  # bound to the finest clause
    assert absolute.penalty_min == 200_000
    assert absolute.penalty_max == 1_000_000
    assert absolute.effective_from == "2015-09-01"
    assert {"国家级", "最高级", "最佳"} <= set(absolute.canonical_terms)
    # 执法指南 五、六 两节共 9 项豁免
    assert len(absolute.context_exemptions) == 9
    assert any("自我比较" in exemption for exemption in absolute.context_exemptions)

    false_ad = all_rules["ADV-LAW-ART28"]
    assert false_ad.family == "false_advertising"
    assert false_ad.penalty_min == 200_000
    assert false_ad.penalty_max == 1_000_000


def test_guideline_has_no_article_clauses(parsed):
    docs, _ = parsed
    assert docs["ADV-GUIDE-ABS"].clauses == []
