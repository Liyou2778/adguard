#!/usr/bin/env bash
# Run DPO on top of the SFT adapter. Usage: bash run_dpo.sh [sft_adapter_dir]
set -euo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json
ADAPTER="${1:-runs/sft-qwen25-3b-qlora-v1/artifacts/final}"
python - <<'PY'
import json
cfg = json.loads(open("configs/dpo_qwen25_3b_qlora.json").read())
cfg["model_id"] = "/root/models/qwen2.5-3b-instruct"
json.dump(cfg, open("/tmp/dpo_config_local.json", "w"), ensure_ascii=False, indent=2)
PY
.venv/bin/python -m adguard_training.train_dpo \
  --config /tmp/dpo_config_local.json \
  --data-dir /root/adguard-train/data/generated \
  --sft-adapter "$ADAPTER" \
  --reports-dir /root/adguard-train/reports
