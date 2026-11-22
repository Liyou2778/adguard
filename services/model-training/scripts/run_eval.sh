#!/usr/bin/env bash
# Evaluate model/adapter. Usage: bash run_eval.sh <adapter_dir|none> [split] [name]
set -euo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json
ADAPTER="${1:-none}"
SPLIT="${2:-eval}"
NAME="${3:-eval-${SPLIT}-$(basename "$ADAPTER" 2>/dev/null || echo base)}"
MODEL_DIR="${4:-/root/models/qwen2.5-3b-instruct}"
EXTRA=""
if [ "$ADAPTER" != "none" ]; then EXTRA="--adapter $ADAPTER"; fi
.venv/bin/python -m adguard_training.evaluate \
  --model-id "$MODEL_DIR" $EXTRA --split "$SPLIT" \
  --data-dir /root/adguard-train/data/generated \
  --name "$NAME"
