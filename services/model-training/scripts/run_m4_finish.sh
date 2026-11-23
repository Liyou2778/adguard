#!/usr/bin/env bash
# M4 finish chain: short DPO (400 pairs) -> DPO eval -> distillation -> student eval.
set -uo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json

CHAIN_LOG=/root/adguard-train/m4_finish.log
exec >> "$CHAIN_LOG" 2>&1
echo "=== M4 finish chain start $(date) ==="

SFT_ADAPTER=$(realpath reports/experiments/sft-qwen25-3b-qlora-v1/artifacts/final)

# deterministic 400-pair subset (local 8GB time budget; full 1242 pairs -> cloud GPU)
mkdir -p data/dpo_short
head -400 data/generated/dpo_pairs.jsonl > data/dpo_short/dpo_pairs.jsonl
cp data/generated/manifest.json data/dpo_short/manifest.json
.venv/bin/python - <<'PY'
import json
cfg = json.load(open('configs/dpo_qwen25_3b_qlora.json'))
cfg['model_id'] = '/root/models/qwen2.5-3b-instruct'
cfg['train']['epochs'] = 1
cfg['train']['save_steps'] = 25
json.dump(cfg, open('/tmp/dpo_short.json', 'w'), ensure_ascii=False, indent=2)
PY

echo "=== DPO short start $(date) pairs=400 ==="
.venv/bin/python -m adguard_training.train_dpo \
  --config /tmp/dpo_short.json \
  --data-dir /root/adguard-train/data/dpo_short \
  --sft-adapter "$SFT_ADAPTER" \
  --reports-dir /root/adguard-train/reports > /root/adguard-train/dpo_short.log 2>&1
echo "DPO_SHORT_EXIT=$?"
tail -4 /root/adguard-train/dpo_short.log

DPO_ADAPTER=/root/adguard-train/reports/experiments/dpo-qwen25-3b-qlora-v1/artifacts/final
if [ -f "$DPO_ADAPTER/adapter_config.json" ]; then
  echo "=== DPO eval start $(date) ==="
  bash run_eval.sh "$DPO_ADAPTER" eval eval-dpo 2>&1 | tail -45
  echo "DPO_EVAL_EXIT=${PIPESTATUS[0]}"
else
  echo "DPO_ADAPTER_MISSING"
fi

echo "=== distillation start $(date) ==="
bash run_distill.sh > /root/adguard-train/distill.log 2>&1
echo "DISTILL_EXIT=$?"
tail -6 /root/adguard-train/distill.log

echo "=== M4 finish chain done $(date) ==="
