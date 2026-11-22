"""Evaluate a judge model against the M4 gates (recall / false-positive / clause hit).

Batched generation keeps a full 535-row eval run practical on an 8 GB laptop GPU.

    python -m adguard_training.evaluate --model-id /root/models/qwen2.5-3b-instruct \
        --adapter runs/.../final --split eval --batch-size 8

Writes reports/experiments/<name>/metrics.json and exits non-zero when a gate fails.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # noqa: E402

from .prompts import system_prompt, user_prompt  # noqa: E402
from .registry import finish_run, start_run  # noqa: E402
from .schema import OutputParseError, parse_output  # noqa: E402

GATES = {"recall": 0.95, "false_positive_rate": 0.03, "wrong_clause_rate": 0.0, "json_parse_rate": 0.99}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_model(model_id: str, adapter: Path | None):
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, quantization_config=bnb, device_map={"": 0}, trust_remote_code=True
    )
    if adapter is not None:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(adapter))
    model.eval()
    return model, tokenizer


def build_prompt(text: str, asset_type: str = "text") -> str:
    # MUST be identical to the training prompt (prompts.build_record) - otherwise the
    # model sees a different system prompt at eval time and metrics measure nothing.
    return [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_prompt(text, asset_type)},
    ]


@torch.no_grad()
def generate_batch(model, tokenizer, texts: list[str], max_new_tokens: int) -> list[str]:
    prompts = [tokenizer.apply_chat_template(build_prompt(text), tokenize=False, add_generation_prompt=True)
               for text in texts]
    inputs = tokenizer(prompts, return_tensors="pt", padding=True, truncation=True, max_length=1536).to(model.device)
    output = model.generate(
        **inputs, max_new_tokens=max_new_tokens, do_sample=False,
        temperature=None, top_p=None, pad_token_id=tokenizer.pad_token_id,
    )
    generated = output[:, inputs["input_ids"].shape[1]:]
    return tokenizer.batch_decode(generated, skip_special_tokens=True)


def evaluate(model_id: str, adapter: Path | None, split: str, data_dir: Path,
             name: str, max_new_tokens: int = 192, batch_size: int = 8,
             limit: int | None = None) -> dict[str, Any]:
    rows = load_jsonl(data_dir / f"{split}.jsonl")
    if limit:
        rows = rows[:limit]
    run_dir = start_run(name, {"model_id": model_id, "adapter": str(adapter or ""), "split": split,
                               "data_dir": str(data_dir), "max_new_tokens": max_new_tokens,
                               "batch_size": batch_size}, data_dir)
    model, tokenizer = load_model(model_id, adapter)

    totals = {"rows": 0, "gold_block": 0, "gold_pass": 0, "parsed": 0, "invalid": 0,
              "block_predicted": 0, "true_positive": 0, "false_positive": 0,
              "violations_emitted": 0, "wrong_citations": 0, "exact_clause_rows": 0}
    samples: list[dict[str, Any]] = []

    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        raw_outputs = generate_batch(model, tokenizer, [row["text"] for row in batch], max_new_tokens)
        for row, raw in zip(batch, raw_outputs):
            totals["rows"] += 1
            if row["decision"] == "block":
                totals["gold_block"] += 1
            else:
                totals["gold_pass"] += 1
            try:
                parsed = parse_output(raw)
                totals["parsed"] += 1
            except OutputParseError as exc:
                totals["invalid"] += 1
                if len(samples) < 40:
                    samples.append({"id": row["id"], "error": str(exc), "raw": raw[:200]})
                continue
            if parsed.decision == "block":
                totals["block_predicted"] += 1
                if row["decision"] == "block":
                    totals["true_positive"] += 1
                else:
                    totals["false_positive"] += 1
            if parsed.violations:
                totals["violations_emitted"] += len(parsed.violations)
                if row["decision"] == "block":
                    gold_clauses = {v["clause_id"] for v in row["violations"]}
                    pred_clauses = {v.clause_id for v in parsed.violations}
                    if not pred_clauses.issubset(gold_clauses):
                        totals["wrong_citations"] += len(pred_clauses - gold_clauses)
                    else:
                        totals["exact_clause_rows"] += 1
                else:
                    totals["wrong_citations"] += len(parsed.violations)
            if len(samples) < 40 and (row["decision"] == "block") != (parsed.decision == "block"):
                samples.append({"id": row["id"], "text": row["text"], "gold": row["decision"],
                                "pred": parsed.decision, "raw": raw[:200]})
        print(f"[eval] {min(start + batch_size, len(rows))}/{len(rows)}", flush=True)

    recall = totals["true_positive"] / totals["gold_block"] if totals["gold_block"] else 0.0
    fpr = totals["false_positive"] / totals["gold_pass"] if totals["gold_pass"] else 0.0
    wrong_clause_rate = totals["wrong_citations"] / totals["violations_emitted"] if totals["violations_emitted"] else 0.0
    parse_rate = totals["parsed"] / totals["rows"] if totals["rows"] else 0.0

    metrics = {
        "split": split,
        "totals": totals,
        "recall": round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "wrong_clause_rate": round(wrong_clause_rate, 4),
        "json_parse_rate": round(parse_rate, 4),
        "gates": GATES,
        "gate_checks": {
            "recall >= 0.95": recall >= GATES["recall"],
            "false_positive_rate <= 0.03": fpr <= GATES["false_positive_rate"],
            "wrong_clause_rate == 0": wrong_clause_rate <= GATES["wrong_clause_rate"],
            "json_parse_rate >= 0.99": parse_rate >= GATES["json_parse_rate"],
        },
        "sample_errors": samples,
    }
    metrics["gate_passed"] = all(metrics["gate_checks"].values())
    finish_run(run_dir, metrics, artifacts={"run_dir": str(run_dir)})
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AdGuard judge model")
    parser.add_argument("--model-id", default="/root/models/qwen2.5-3b-instruct")
    parser.add_argument("--adapter", type=Path, default=None)
    parser.add_argument("--split", default="eval")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--max-new-tokens", type=int, default=192)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    name = args.name or f"eval-{args.split}-{(args.adapter.name if args.adapter else 'base')}"
    metrics = evaluate(args.model_id, args.adapter, args.split, args.data_dir, name,
                       args.max_new_tokens, args.batch_size, args.limit)
    print(json.dumps({k: metrics[k] for k in
                      ("split", "recall", "false_positive_rate", "wrong_clause_rate",
                       "json_parse_rate", "gate_checks", "gate_passed", "totals")},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if metrics["gate_passed"] else 1)


if __name__ == "__main__":
    main()
