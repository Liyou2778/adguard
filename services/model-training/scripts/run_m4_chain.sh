#!/usr/bin/env bash
# M4 chain v2: base eval -> SFT eval -> DPO -> DPO eval -> distillation -> student eval.
# Per-stage logs (eval-base.log, eval-sft.log, dpo.log, ...) + exit markers in m4_chain2.log.
set -uo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json

CHAIN_LOG=/root/adguard-train/m4_chain2.log
exec >> "$CHAIN_LOG" 2>&1
echo "=== M4 chain2 start $(date) ==="

SFT_ADAPTER=/root/adguard-train/reports/experiments/sft-qwen25-3b-qlora-v1/artifacts/final
DPO_ADAPTER=/root/adguard-train/reports/experiments/dpo-qwen25-3b-qlora-v1/artifacts/final

run_stage() {
  local name="$1"; shift
  echo "=== $name start $(date) ==="
  "$@" > "/root/adguard-train/${name}.log" 2>&1
  local code=$?
  echo "${name}_EXIT=${code}"
  tail -3 "/root/adguard-train/${name}.log" 2>/dev/null || true
}

run_stage eval-base bash run_eval.sh none eval eval-base
run_stage eval-sft bash run_eval.sh "$SFT_ADAPTER" eval eval-sft
run_stage dpo bash run_dpo.sh "$SFT_ADAPTER"
if [ -f "$DPO_ADAPTER/adapter_config.json" ]; then
  run_stage eval-dpo bash run_eval.sh "$DPO_ADAPTER" eval eval-dpo
else
  echo "DPO_ADAPTER_MISSING"
fi
run_stage distill bash run_distill.sh
echo "=== M4 chain2 done $(date) ==="
