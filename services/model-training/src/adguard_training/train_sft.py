"""QLoRA SFT for the AdGuard judging model (runs inside WSL2 CUDA environment).

Usage (WSL):
    cd /root/adguard-train
    .venv/bin/python -m adguard_training.train_sft --config configs/sft_qwen25_3b_qlora.json

The run directory (config + data manifest + metrics) is written to
services/model-training/reports/experiments/<name>/ so every experiment is reproducible.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch  # noqa: E402
from datasets import Dataset, load_dataset  # noqa: E402
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # noqa: E402
from transformers import (  # noqa: E402
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from trl import SFTTrainer  # noqa: E402

from .prompts import build_record, load_rule_catalog  # noqa: E402
from .registry import finish_run, start_run  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def to_text_dataset(rows: list[dict], tokenizer, rule_catalog: str) -> list[dict]:
    dataset = []
    for row in rows:
        messages = build_record(row["text"], row["output"], rule_catalog=rule_catalog)["messages"]
        rendered = tokenizer.apply_chat_template(messages, tokenize=False)
        dataset.append({"text": rendered})
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="QLoRA SFT for AdGuard judge model")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_dir = start_run(config["name"], config, args.data_dir, notes=config.get("notes", ""))
    print(f"[sft] run dir: {run_dir}")

    tokenizer = AutoTokenizer.from_pretrained(config["model_id"], trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        config["model_id"],
        quantization_config=bnb_config,
        device_map={"": 0},
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    lora = LoraConfig(
        r=config["lora"]["r"],
        lora_alpha=config["lora"]["alpha"],
        lora_dropout=config["lora"]["dropout"],
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=config["lora"]["target_modules"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    rule_catalog = load_rule_catalog()
    train_rows = load_jsonl(args.data_dir / "sft_train.jsonl")
    val_rows = load_jsonl(args.data_dir / "sft_val.jsonl")
    train_dataset = Dataset.from_list(to_text_dataset(train_rows, tokenizer, rule_catalog))
    val_dataset = Dataset.from_list(to_text_dataset(val_rows, tokenizer, rule_catalog))

    training_args = TrainingArguments(
        output_dir=str(run_dir / "artifacts"),
        per_device_train_batch_size=config["train"]["batch_size"],
        gradient_accumulation_steps=config["train"]["grad_accum"],
        num_train_epochs=config["train"]["epochs"],
        learning_rate=config["train"]["learning_rate"],
        lr_scheduler_type=config["train"]["lr_scheduler"],
        warmup_ratio=config["train"]["warmup_ratio"],
        logging_steps=config["train"]["logging_steps"],
        save_steps=config["train"]["save_steps"],
        save_total_limit=2,
        eval_strategy=config["train"].get("eval_strategy", "no"),
        eval_steps=config["train"]["save_steps"],
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        optim="paged_adamw_8bit",
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=[],
        seed=config["train"]["seed"],
        dataloader_num_workers=0,
    )
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        args=training_args,
        max_seq_length=config["train"]["max_seq_length"],
        dataset_text_field="text",
        packing=False,
    )
    train_result = trainer.train()
    trainer.save_model(str(run_dir / "artifacts" / "final"))
    tokenizer.save_pretrained(str(run_dir / "artifacts" / "final"))

    metrics = {
        "train_loss": train_result.training_loss,
        "train_runtime_seconds": train_result.metrics.get("train_runtime"),
        "train_samples": len(train_rows),
        "val_samples": len(val_rows),
        "epochs": config["train"]["epochs"],
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 2) if torch.cuda.is_available() else 0,
    }
    finish_run(run_dir, metrics, artifacts={"adapter": str(run_dir / "artifacts" / "final")})
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
