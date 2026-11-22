from __future__ import annotations

from adguard_pipeline.normalization import Normalizer


def make() -> Normalizer:
    return Normalizer.from_data()


def test_nfkc_fullwidth_is_folded():
    normalizer = make()
    result = normalizer.normalize("ＡＢＣ１２３")
    assert result.text == "abc123"


def test_zero_width_characters_are_stripped_but_offsets_survive():
    normalizer = make()
    raw = "最\u200b好"
    result = normalizer.normalize(raw)
    assert result.text == "最好"
    assert result.to_original_span(0, 2) == (0, 3)


def test_emoji_separators_are_stripped():
    normalizer = make()
    result = normalizer.normalize("最😀好🔥")
    assert result.text == "最好"


def test_traditional_converts_to_simplified():
    normalizer = make()
    assert normalizer.normalize("國家級").text == "国家级"
    assert normalizer.normalize("頂級").text == "顶级"


def test_variant_characters_map_to_canonical():
    normalizer = make()
    assert normalizer.normalize("冣好").text == "最好"
    assert normalizer.normalize("羙").text == "美"


def test_component_sequence_is_merged_with_merged_span():
    normalizer = make()
    raw = "曰取好"
    result = normalizer.normalize(raw)
    assert result.text == "最好"
    # normalized '最' came from original chars 0..1
    assert result.to_original_span(0, 1) == (0, 2)
    assert result.to_original_span(0, 2) == (0, 3)


def test_pinyin_tone_marks_fold_to_plain_letters():
    normalizer = make()
    result = normalizer.normalize("zuì hǎo")
    assert result.compact == "zuihao"
    assert result.text == "zui hao"


def test_compact_strips_chinese_and_punctuation():
    normalizer = make()
    result = normalizer.normalize("最好 zui4hao3 ！")
    assert "最好" not in result.compact
    assert "zui4hao3" in result.compact


def test_span_roundtrip_without_evasion_is_identity():
    normalizer = make()
    result = normalizer.normalize("本店最好")
    assert result.text == "本店最好"
    assert result.to_original_span(2, 4) == (2, 4)
