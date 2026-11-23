#!/usr/bin/env bash
# Cloud stage 4: merge adapters into fp16 7B for serving; collect eval metrics of both adapters.
set -uo pipefail
BASE=/root/adguard-m5
LOG=$BASE/logs/stage4_merge.log
exec >> "$LOG" 2>&1
echo "=== stage4 merge start $(date) ==="
source "$BASE/venv/bin/activate"
export PYTHONPATH="$BASE/repo/services/model-training/src"

SFT_ADAPTER=$BASE/reports/experiments/sft-qwen25-7b-qlora-cloud-v1/artifacts/final
DPO_ADAPTER=$BASE/reports/experiments/dpo-qwen25-7b-qlora-cloud-v1/artifacts/final

if [ -f "$SFT_ADAPTER/adapter_config.json" ]; then
  python -m adguard_training.merge_adapter --base "$BASE/models/qwen2.5-7b-instruct" \
    --adapter "$SFT_ADAPTER" --output "$BASE/models/merged-sft-7b"
  echo "SFT_MERGE_EXIT=$?"
fi
if [ -f "$DPO_ADAPTER/adapter_config.json" ]; then
  python -m adguard_training.merge_adapter --base "$BASE/models/qwen2.5-7b-instruct" \
    --adapter "$DPO_ADAPTER" --output "$BASE/models/merged-dpo-7b"
  echo "DPO_MERGE_EXIT=$?"
fi
du -sh "$BASE/models"/* 2>/dev/null
echo "STAGE4_DONE $(date)"
