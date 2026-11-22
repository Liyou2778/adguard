"""Merge a LoRA adapter into the base model so vLLM/SGLang can serve it.

vLLM cannot load a 4-bit QLoRA adapter directly; M5 pipeline:
    adapter -> merge (fp16) -> optional AWQ/GPTQ quantize -> vLLM/SGLang

Usage (WSL, after M4 SFT/DPO):
    python -m adguard_training.merge_adapter \
        --base /root/models/qwen2.5-3b-instruct \
        --adapter /root/adguard-train/reports/experiments/sft-qwen25-3b-qlora-v1/artifacts/final \
        --output /root/models/adguard-judge-3b-merged
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def merge(base_model: str, adapter: Path, output: Path, dtype: str = "float16") -> None:
    print(f"[merge] loading base {base_model} (CPU, {dtype})")
    torch_dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}[dtype]
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch_dtype, device_map="cpu", trust_remote_code=True
    )
    print(f"[merge] applying adapter {adapter}")
    model = PeftModel.from_pretrained(model, str(adapter))
    model = model.merge_and_unload()
    model.save_pretrained(str(output), safe_serialization=True)
    AutoTokenizer.from_pretrained(base_model, trust_remote_code=True).save_pretrained(str(output))
    size_gb = sum(p.stat().st_size for p in output.glob("*.safetensors")) / 1024**3
    print(f"[merge] saved {output} ({size_gb:.2f} GiB safetensors)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge LoRA adapter into base model")
    parser.add_argument("--base", required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dtype", choices=["float16", "bfloat16"], default="float16")
    args = parser.parse_args()
    merge(args.base, args.adapter, args.output, args.dtype)


if __name__ == "__main__":
    main()
