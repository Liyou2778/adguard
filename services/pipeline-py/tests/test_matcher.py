from __future__ import annotations

import pytest

from adguard_pipeline.matcher import Matcher


@pytest.fixture(scope="module")
def matcher() -> Matcher:
    return Matcher()


def hits(result):
    return [(d.family, d.canonical, d.variant_kind, d.evidence) for d in result.detections]


def test_plain_violation_is_detected_with_offsets(matcher):
    text = "本店商品最好，欢迎选购"
    result = matcher.match(text)
    assert hits(result) == [("absolute_terms", "最好", "canonical", "最好")]
    detection = result.detections[0]
    assert text[detection.span[0] : detection.span[1]] == "最好"


def test_zero_width_and_emoji_evasion_still_maps_to_original_span(matcher):
    text = "本店最\u200b😀佳，速来"
    result = matcher.match(text)
    assert result.hit
    detection = result.detections[0]
    assert detection.canonical == "最佳"
    assert detection.evidence == "最\u200b😀佳"
    assert text[detection.span[0] : detection.span[1]] == "最\u200b😀佳"


def test_pinyin_variant_is_detected(matcher):
    result = matcher.match("全网 zui hao 的价格")
    assert result.hit
    assert result.detections[0].canonical == "最好"
    assert result.detections[0].variant_kind == "pinyin"


def test_pinyin_with_tones_and_spaces_is_detected(matcher):
    result = matcher.match("quality: zui4 hao3")
    assert result.hit
    assert result.detections[0].canonical == "最好"


def test_traditional_and_homophone_variants(matcher):
    assert matcher.match("這是國家級認證").hit
    homophone = matcher.match("醉好的选择")
    assert homophone.hit
    assert homophone.detections[0].canonical == "最好"
    assert homophone.detections[0].variant_kind == "homophone"


def test_synonym_phrase_is_detected(matcher):
    result = matcher.match("这款产品无出其右")
    assert result.hit
    assert result.detections[0].variant_kind == "synonym"


def test_compliant_copy_has_no_detection(matcher):
    result = matcher.match("本店商品质量可靠，支持七天无理由退换。")
    assert not result.hit, hits(result)


def test_longest_detection_wins_when_nested(matcher):
    result = matcher.match("国家级产品")
    # "国家级" must win over the shorter "国家" style partial matches if any.
    assert result.detections[0].canonical == "国家级"


def test_version_exposes_dictionary_and_pattern_count(matcher):
    assert matcher.version.startswith("dict:absolute_terms:v1")
    assert matcher.pattern_count > 100
