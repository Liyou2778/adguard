"""FR-6 acceptance evaluation: run the consistency engine over the constructed pairs.

Usage:
  python scripts/eval_consistency.py [--rules-only] [--out reports/consistency_eval.json]

The audit requirement is recall >= 0.90 on constructed conflict pairs with a low false-positive
rate on non-conflicting pairs. With the LLM configured (ADGUARD_CONSISTENCY_LLM_*), gray-zone
semantic conflicts are covered by the LLM fallback; --rules-only measures the deterministic
baseline honestly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adguard_pipeline.consistency import check_consistency  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules-only", action="store_true", help="disable the LLM gray-zone fallback")
    parser.add_argument("--out", default=str(ROOT / "reports" / "consistency_eval.json"))
    args = parser.parse_args()

    dataset_path = ROOT / "data" / "eval" / "consistency_pairs.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    pairs = dataset["pairs"]

    positives = [p for p in pairs if p["expect_conflict"]]
    negatives = [p for p in pairs if not p["expect_conflict"]]
    tp = fn = fp = tn = 0
    details = []
    for pair in pairs:
        result = check_consistency(pair["caption"], pair["image_text"], use_llm=not args.rules_only)
        predicted = bool(result.conflicts)
        expected = bool(pair["expect_conflict"])
        hit = predicted == expected
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif not expected and predicted:
            fp += 1
        else:
            tn += 1
        details.append({
            "id": pair["id"], "tag": pair["tag"], "expected": expected, "predicted": predicted,
            "correct": hit, "llm_used": result.llm_used, "degraded": result.degraded,
            "conflicts": [c.to_dict() for c in result.conflicts],
            "warnings": result.warnings,
        })

    recall = tp / (tp + fn) if positives else 0.0
    false_positive_rate = fp / (fp + tn) if negatives else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    report = {
        "dataset_version": dataset.get("version"),
        "mode": "rules" if args.rules_only else "rules+llm",
        "llm_configured": bool(os.environ.get("ADGUARD_CONSISTENCY_LLM_URL")
                               and os.environ.get("ADGUARD_CONSISTENCY_LLM_KEY")),
        "pairs": len(pairs),
        "positives": len(positives),
        "negatives": len(negatives),
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "recall": round(recall, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "precision": round(precision, 4),
        "gate": {"recall_min": 0.9, "fpr_max": 0.1, "pass": recall >= 0.9 and false_positive_rate <= 0.1},
        "details": details,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"mode={report['mode']} pairs={report['pairs']} "
          f"recall={report['recall']:.4f} fpr={report['false_positive_rate']:.4f} "
          f"precision={report['precision']:.4f}")
    for item in details:
        if not item["correct"]:
            print(f"  MISS {item['id']} ({item['tag']}) expected={item['expected']} "
                  f"predicted={item['predicted']} warnings={item['warnings']}")
    print(f"report -> {out}")
    return 0 if report["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
