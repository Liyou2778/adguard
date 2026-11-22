from __future__ import annotations

from adguard_pipeline.dataset import generate, stats
from adguard_pipeline.matcher import Matcher


def test_dataset_is_deterministic():
    first = generate()
    second = generate()
    assert [s.id for s in first] == [s.id for s in second]
    assert [s.text for s in first] == [s.text for s in second]


def test_variant_set_meets_prd_size():
    samples = generate()
    breakdown = stats(samples)
    variant_total = sum(breakdown["variant"].values())
    assert variant_total >= 1000, f"PRD M2 requires >=1000 variant samples, got {variant_total}"
    # Every promised family has a meaningful share (not a single-family dataset).
    for family in (
        "canonical",
        "pinyin",
        "emoji",
        "zero_width",
        "homophone",
        "split_char",
        "variant_char",
        "synonym",
    ):
        assert breakdown["variant"].get(family, 0) >= 60, family


def test_planted_span_is_detected_by_the_matcher():
    matcher = Matcher()
    samples = [s for s in generate() if s.kind == "variant"]
    for sample in samples[:300]:
        planted = (sample.span_start, sample.span_end)
        assert sample.text[planted[0] : planted[1]], sample.id
        result = matcher.match(sample.text)
        overlapped = [
            d
            for d in result.detections
            if d.family == sample.expected_family
            and max(0, min(d.span[1], planted[1]) - max(d.span[0], planted[0])) > 0
        ]
        assert overlapped, (sample.id, sample.variant_family, sample.text)


def test_negative_samples_are_free_of_dictionary_terms():
    matcher = Matcher()
    negatives = [s for s in generate() if s.kind == "negative"]
    assert len(negatives) == 200
    false_positives = [s for s in negatives if matcher.match(s.text).hit]
    assert not false_positives, false_positives[:3]
