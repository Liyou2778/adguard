"""DPO training on top of the SFT adapter (preference pairs from the rule teacher).

PRD ADR-4 wants preference pairs from real review decisions; M4 local starts with
synthetic pairs (correct vs. plausible error) and M5 replaces them with review data.
Run inside WSL2 CUDA:

    .venv/bin/python -m adguard_training.train_dpo --config configs/dpo_qwen25_3b_qlora.json \
        --data-dir data/generated --sft-adapter runs/sft-qwen25-3b-qlora-v1/artifacts/final
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from peft import LoraConfig, PeftModel  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # noqa: E402
from trl import DPOConfig, DPOTrainer  # noqa: E402

from .prompts import build_record  # noqa: E402
from .registry import finish_run, start_run  # noqa: E402


def load_pairs(path: Path) -> list[dict]:
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        prompt = build_record(row["text"], "")["messages"][:2]
        pairs.append({"prompt": prompt, "chosen": row["chosen"], "rejected": row["rejected"]})
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="DPO for AdGuard judge model")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--sft-adapter", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_dir = start_run(config["name"], config, args.data_dir, notes=config.get("notes", ""))
    print(f"[dpo] run dir: {run_dir}")

    tokenizer = AutoTokenizer.from_pretrained(config["model_id"], trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        config["model_id"], quantization_config=bnb, device_map={"": 0}, trust_remote_code=True
    )
    model = PeftModel.from_pretrained(model, str(args.sft_adapter), is_trainable=True)

    def format_pairs(pairs):
        formatted = []
        for pair in pairs:
            prompt = tokenizer.apply_chat_template(
                pair["prompt"], tokenize=False, add_generation_prompt=True
            )
            formatted.append({"prompt": prompt, "chosen": pair["chosen"], "rejected": pair["rejected"]})
        return formatted

    dataset = Dataset.from_list(format_pairs(load_pairs(args.data_dir / "dpo_pairs.jsonl")))
    split = dataset.train_test_split(test_size=0.05, seed=config["train"]["seed"])

    training_args = DPOConfig(
        output_dir=str(run_dir / "artifacts"),
        per_device_train_batch_size=config["train"]["batch_size"],
        gradient_accumulation_steps=config["train"]["grad_accum"],
        num_train_epochs=config["train"]["epochs"],
        learning_rate=config["train"]["learning_rate"],
        lr_scheduler_type="cosine",
        warmup_ratio=config["train"]["warmup_ratio"],
        logging_steps=10,
        save_steps=config["train"]["save_steps"],
        save_total_limit=1,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        beta=config["train"]["beta"],
        max_length=config["train"]["max_length"],
        max_prompt_length=config["train"]["max_prompt_length"],
        report_to=[],
        seed=config["train"]["seed"],
        remove_unused_columns=False,
    )
    trainer = DPOTrainer(
        model=model,
        ref_model=None,  # PEFT adapter: reference = adapter-disabled base, saves VRAM
        args=training_args,
        train_dataset=split["train"],
        eval_dataset=split["test"],
        tokenizer=tokenizer,
    )
    train_result = trainer.train()
    trainer.save_model(str(run_dir / "artifacts" / "final"))
    tokenizer.save_pretrained(str(run_dir / "artifacts" / "final"))

    metrics = {
        "train_loss": train_result.training_loss,
        "train_runtime_seconds": train_result.metrics.get("train_runtime"),
        "pairs": len(dataset),
        "beta": config["train"]["beta"],
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 2),
    }
    finish_run(run_dir, metrics, artifacts={"adapter": str(run_dir / "artifacts" / "final")})
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
