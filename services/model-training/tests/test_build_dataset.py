from __future__ import annotations

import json
from pathlib import Path

import pytest

from adguard_training.build_dataset import build
from adguard_training.schema import parse_output

GENERATED = Path(__file__).resolve().parents[1] / "data" / "generated"


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    out = tmp_path_factory.mktemp("m4-data")
    return build(out), out


def test_split_sizes_and_balance(manifest):
    data, _ = manifest
    splits = data["splits"]
    assert splits["sft_train"]["rows"] > 1000
    assert splits["eval"]["rows"] > 400
    assert splits["eval"]["decisions"]["block"] > 0
    assert splits["eval"]["decisions"]["pass"] > 0
    assert splits["dpo_pairs"]["rows"] > 1000


def test_eval_and_train_have_no_text_overlap(manifest):
    _, out = manifest
    def texts(name: str) -> set[str]:
        return {json.loads(line)["text"]
                for line in (out / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()}
    train_texts = texts("sft_train") | texts("sft_val")
    eval_texts = texts("eval")
    assert not (train_texts & eval_texts)


def test_all_block_gold_outputs_are_schema_valid(manifest):
    _, out = manifest
    for name in ("sft_train", "sft_val", "eval"):
        for line in (out / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["decision"] == "block":
                parsed = parse_output(row["output"])
                assert parsed.decision == "block"
                assert parsed.violations, row["id"]


def test_case_seed_coverage_is_recorded(manifest):
    data, _ = manifest
    coverage = data["case_coverage"]
    assert any("covered_phrases" in item for item in coverage)
    gaps = [item for item in coverage if item.get("status") == "dictionary_gap"]
    assert gaps, "dictionary gaps must be recorded instead of silently dropped"
