"""Distillation: teacher (SFT 3B) generates labels -> student (0.5B) imitates.

M4 runs this locally to prove the distillation *pipeline* end to end on 8 GB VRAM
(teacher 3B 4-bit -> student 0.5B). M5 repeats it at the planned scale
(teacher 7B/8B on cloud GPU -> student 3B) with the same code and gates.

Usage:
    # 1) teacher generates outputs on the training prompts
    .venv/bin/python -m adguard_training.distill generate \
        --teacher-adapter runs/sft-qwen25-3b-qlora-v1/artifacts/final \
        --data-dir data/generated --out-dir data/distill

    # 2) train the student on teacher outputs (same SFT code, different config)
    .venv/bin/python -m adguard_training.train_sft --config configs/distill_qwen05_student.json \
        --data-dir data/distill
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch  # noqa: E402
from peft import PeftModel  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # noqa: E402

from .evaluate import generate_batch  # noqa: E402
from .registry import finish_run, start_run  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_teacher(model_id: str, adapter: Path):
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    tokenizer.padding_side = "left"  # batched generation requires left padding
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, quantization_config=bnb, device_map={"": 0}, trust_remote_code=True
    )
    model = PeftModel.from_pretrained(model, str(adapter))
    model.eval()
    return model, tokenizer


def generate_distill_data(teacher_model_id: str, teacher_adapter: Path,
                          data_dir: Path, out_dir: Path, max_new_tokens: int,
                          limit: int | None = None, batch_size: int = 8) -> Path:
    rows = load_jsonl(data_dir / "sft_train.jsonl")
    if limit:
        rows = rows[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    model, tokenizer = load_teacher(teacher_model_id, teacher_adapter)
    outputs: list[dict] = []
    total = len(rows)
    for start in range(0, total, batch_size):
        batch = rows[start:start + batch_size]
        generated_batch = generate_batch(model, tokenizer, [row["text"] for row in batch], max_new_tokens)
        for offset, (row, generated) in enumerate(zip(batch, generated_batch), start=start + 1):
            outputs.append(
                {
                    "id": f"distill-{offset:05d}",
                    "kind": row["kind"],
                    "source": "teacher-sft-3b",
                    "text": row["text"],
                    "decision": row["decision"],  # gold for stratification only
                    "violations": row["violations"],
                    "output": generated,
                }
            )
        print(f"[distill] generated {min(start + batch_size, total)}/{total}", flush=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in outputs) + "\n"
    (out_dir / "sft_train.jsonl").write_text(payload, encoding="utf-8")
    shutil.copyfile(data_dir / "sft_val.jsonl", out_dir / "sft_val.jsonl")
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["distillation"] = {
        "teacher_model": teacher_model_id,
        "teacher_adapter": str(teacher_adapter),
        "rows": len(outputs),
        "note": "labels are teacher generations, not rule gold; eval set unchanged",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Distill teacher outputs for student training")
    parser.add_argument("command", choices=["generate"])
    parser.add_argument("--teacher-model-id", default="/root/models/qwen2.5-3b-instruct")
    parser.add_argument("--teacher-adapter", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    run_dir = start_run("distill-data-v1", {
        "teacher_model": args.teacher_model_id,
        "teacher_adapter": str(args.teacher_adapter),
        "limit": args.limit,
        "max_new_tokens": args.max_new_tokens,
    }, args.data_dir, notes="teacher-generated SFT data for student distillation")
    generate_distill_data(args.teacher_model_id, args.teacher_adapter, args.data_dir,
                          args.out_dir, args.max_new_tokens, args.limit)
    finish_run(run_dir, {"rows": len(load_jsonl(args.out_dir / "sft_train.jsonl"))},
               artifacts={"out_dir": str(args.out_dir)})
    print(f"distill data written to {args.out_dir}")


if __name__ == "__main__":
    main()
