from __future__ import annotations

from adguard_pipeline.retrieval.bm25 import CharBigramBM25, tokenize


def test_tokenize_chinese_bigrams():
    tokens = tokenize("广告不得使用国家级")
    assert "广告" in tokens
    assert "国家" in tokens
    assert "家级" in tokens


def test_bm25_ranks_matching_document_first():
    bm25 = CharBigramBM25().fit({
        "a": "使用“国家级”“最高级”“最佳”等用语",
        "b": "广告不得损害未成年人和残疾人的身心健康",
        "c": "广告应当具有可识别性",
    })
    results = bm25.search("国家级认证", top_k=3)
    assert results, "expected at least one hit"
    assert results[0][0] == "a"
    assert results[0][1] > 0


def test_bm25_returns_empty_for_unrelated_query():
    bm25 = CharBigramBM25().fit({"a": "绝对化用语禁止条款"})
    assert bm25.search("天气不错") == []


def test_bm25_variant_evasion_is_normalized():
    bm25 = CharBigramBM25().fit({"a": "使用国家级最高级最佳等用语"})
    # zero-width characters must not hurt BM25 after normalization
    assert bm25.search("最\u200b佳", top_k=1)[0][0] == "a"
