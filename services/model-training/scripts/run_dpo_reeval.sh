#!/usr/bin/env bash
# Re-run DPO eval with a larger generation budget (outputs were truncated at 192 tokens).
set -uo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json
DPO_ADAPTER=/root/adguard-train/reports/experiments/dpo-qwen25-3b-qlora-v1/artifacts/final
.venv/bin/python -m adguard_training.evaluate \
  --model-id /root/models/qwen2.5-3b-instruct \
  --adapter "$DPO_ADAPTER" \
  --split eval --data-dir /root/adguard-train/data/generated \
  --name eval-dpo-384 --max-new-tokens 384 --batch-size 6
