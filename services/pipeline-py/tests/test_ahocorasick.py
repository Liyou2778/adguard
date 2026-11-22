from __future__ import annotations

import pytest

from adguard_pipeline.ahocorasick import AhoCorasick


def build(*patterns: str) -> AhoCorasick:
    automaton = AhoCorasick()
    for index, pattern in enumerate(patterns):
        automaton.add(pattern, (pattern, index))
    return automaton.build()


def test_basic_match():
    assert build("最好").find_all("本店最好") == [(2, 4, ("最好", 0))]


def test_overlapping_patterns_all_returned():
    automaton = build("abcd", "bc")
    matches = sorted(automaton.find_all("xabcde"))
    assert matches == [(1, 5, ("abcd", 0)), (2, 4, ("bc", 1))]


def test_suffix_pattern_is_reported_via_failure_links():
    automaton = build("he", "she", "hers")
    matches = {payload for _, _, payload in automaton.find_all("ushers")}
    assert ("she", 1) in matches
    assert ("he", 0) in matches
    assert ("hers", 2) in matches


def test_no_false_positive_on_unrelated_text():
    assert build("最好", "最佳").find_all("质量可靠") == []


def test_multiple_occurrences():
    assert len(build("最").find_all("最最好")) == 2


def test_empty_pattern_rejected():
    with pytest.raises(ValueError):
        AhoCorasick().add("", "x")


def test_add_after_build_rejected():
    automaton = AhoCorasick()
    automaton.add("a", "a")
    automaton.build()
    with pytest.raises(RuntimeError):
        automaton.add("b", "b")
