from __future__ import annotations

from bench.bench_client import percentile


def test_percentile_ordering_and_bounds():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 50) == 3.0
    assert percentile(values, 100) == 5.0
    assert percentile([], 50) == 0.0
