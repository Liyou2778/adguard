"""Quality evaluation of a SERVED model through its OpenAI-compatible API.

M5/M6 integration path: the pipeline will call the model over HTTP, so the eval must
exercise the served endpoint too (not just the local HF model). Metrics are identical
to adguard_training.evaluate (recall / FPR / wrong-clause / JSON parse).

    python -m bench.evaluate_api --base-url http://127.0.0.1:8100 \
        --data-dir /root/adguard-train/data/generated --rows 150 --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("ADGUARD_PARSED_RULES", "/root/adguard-train/data/parsers/parsed_m2.json")

from adguard_training.prompts import system_prompt, user_prompt  # noqa: E402
from adguard_training.schema import OutputParseError, parse_output  # noqa: E402

GATES = {"recall": 0.95, "false_positive_rate": 0.03, "wrong_clause_rate": 0.0, "json_parse_rate": 0.99}


async def one(client: httpx.AsyncClient, base_url: str, text: str, max_tokens: int) -> str:
    payload = {
        "model": "adguard-judge",
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": user_prompt(text)},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    response = await client.post(f"{base_url}/v1/chat/completions", json=payload)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


async def run(args) -> dict:
    rows = [json.loads(line) for line in (Path(args.data_dir) / "eval.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()][: args.rows]
    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=180.0) as client:
        async def worker(row: dict) -> None:
            async with semaphore:
                try:
                    raw = await one(client, args.base_url, row["text"], args.max_tokens)
                    results.append({"row": row, "raw": raw})
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")

        await asyncio.gather(*(worker(row) for row in rows))

    totals = {"rows": 0, "gold_block": 0, "gold_pass": 0, "parsed": 0, "invalid": 0,
              "block_predicted": 0, "true_positive": 0, "false_positive": 0,
              "violations_emitted": 0, "wrong_citations": 0, "exact_clause_rows": 0}
    samples: list[dict] = []
    dumps: list[dict] = []
    for item in results:
        row, raw = item["row"], item["raw"]
        totals["rows"] += 1
        totals["gold_block" if row["decision"] == "block" else "gold_pass"] += 1
        try:
            parsed = parse_output(raw)
            totals["parsed"] += 1
        except OutputParseError as exc:
            totals["invalid"] += 1
            if len(samples) < 20:
                samples.append({"id": row["id"], "error": str(exc), "raw": raw[:200]})
            if args.dump_rows is not None:
                dumps.append({"id": row["id"], "gold": row["decision"], "parse_error": str(exc)})
            continue
        pred_clauses: set[str] = set()
        gold_clauses: set[str] = {v["clause_id"] for v in row.get("violations", [])}
        if parsed.decision == "block":
            totals["block_predicted"] += 1
            totals["true_positive" if row["decision"] == "block" else "false_positive"] += 1
        if parsed.violations:
            totals["violations_emitted"] += len(parsed.violations)
            if row["decision"] == "block":
                pred_clauses = {v.clause_id for v in parsed.violations}
                if not pred_clauses.issubset(gold_clauses):
                    totals["wrong_citations"] += len(pred_clauses - gold_clauses)
                else:
                    totals["exact_clause_rows"] += 1
            else:
                pred_clauses = {v.clause_id for v in parsed.violations}
                totals["wrong_citations"] += len(parsed.violations)
        if len(samples) < 20 and (row["decision"] == "block") != (parsed.decision == "block"):
            samples.append({"id": row["id"], "text": row["text"], "gold": row["decision"], "pred": parsed.decision})
        if args.dump_rows is not None:
            dumps.append({
                "id": row["id"],
                "gold": row["decision"],
                "pred": parsed.decision,
                "gold_clauses": sorted(gold_clauses),
                "pred_clauses": sorted(pred_clauses),
                "extra_clauses": sorted(pred_clauses - gold_clauses),
                "missing_clauses": sorted(gold_clauses - pred_clauses),
                "text": row["text"][:120],
                "raw": raw[:400],
            })

    recall = totals["true_positive"] / totals["gold_block"] if totals["gold_block"] else 0.0
    fpr = totals["false_positive"] / totals["gold_pass"] if totals["gold_pass"] else 0.0
    wrong = totals["wrong_citations"] / totals["violations_emitted"] if totals["violations_emitted"] else 0.0
    parse_rate = totals["parsed"] / totals["rows"] if totals["rows"] else 0.0
    metrics = {
        "mode": "served-api",
        "base_url": args.base_url,
        "requested_rows": args.rows,
        "totals": totals,
        "transport_errors": len(errors),
        "error_samples": errors[:5],
        "recall": round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "wrong_clause_rate": round(wrong, 4),
        "json_parse_rate": round(parse_rate, 4),
        "gate_checks": {
            "recall >= 0.95": recall >= GATES["recall"],
            "false_positive_rate <= 0.03": fpr <= GATES["false_positive_rate"],
            "wrong_clause_rate == 0": wrong <= GATES["wrong_clause_rate"],
            "json_parse_rate >= 0.99": parse_rate >= GATES["json_parse_rate"],
        },
        "samples": samples,
    }
    metrics["gate_passed"] = all(metrics["gate_checks"].values()) and len(errors) == 0
    if args.dump_rows is not None:
        args.dump_rows.parent.mkdir(parents=True, exist_ok=True)
        args.dump_rows.write_text(json.dumps(dumps, ensure_ascii=False, indent=1), encoding="utf-8")
        metrics["dump_rows"] = str(args.dump_rows)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate served model through OpenAI API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=150)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dump-rows", type=Path, default=None,
                        help="optional: write per-row predictions (for wrong-citation analysis)")
    args = parser.parse_args()
    metrics = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: metrics[k] for k in ("totals", "recall", "false_positive_rate",
                                               "wrong_clause_rate", "json_parse_rate", "gate_passed")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
