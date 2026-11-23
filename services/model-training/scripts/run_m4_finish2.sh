#!/usr/bin/env bash
# M4 finish (v2): batched distillation (400 teacher samples) -> student train/eval -> DPO re-eval.
set -uo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json

CHAIN_LOG=/root/adguard-train/m4_finish2.log
exec >> "$CHAIN_LOG" 2>&1
echo "=== M4 finish2 chain start $(date) ==="

echo "=== distillation start $(date) ==="
bash run_distill.sh > /root/adguard-train/distill.log 2>&1
echo "DISTILL_EXIT=$?"
tail -8 /root/adguard-train/distill.log

echo "=== DPO re-eval start $(date) (max_new_tokens=384) ==="
bash run_dpo_reeval.sh > /root/adguard-train/dpo_reeval.log 2>&1
echo "DPO_REEVAL_EXIT=$?"
tail -30 /root/adguard-train/dpo_reeval.log

echo "=== M4 finish2 chain done $(date) ==="
