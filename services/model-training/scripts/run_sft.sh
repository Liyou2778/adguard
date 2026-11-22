#!/usr/bin/env bash
# Run QLoRA SFT inside WSL (CUDA). Usage: bash run_sft.sh [config]
set -euo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json
CONFIG="${1:-configs/sft_qwen25_3b_qlora.json}"
MODEL_DIR=/root/models/qwen2.5-3b-instruct

# point model_id at local weights (training is fully offline)
.venv/bin/python - "$CONFIG" "$MODEL_DIR" <<'PY'
import json, sys
cfg_path, model_dir = sys.argv[1], sys.argv[2]
cfg = json.loads(open(cfg_path).read())
cfg["model_id"] = model_dir
json.dump(cfg, open("/tmp/sft_config_local.json", "w"), ensure_ascii=False, indent=2)
PY

.venv/bin/python -m adguard_training.train_sft \
  --config /tmp/sft_config_local.json \
  --data-dir /root/adguard-train/data/generated \
  --reports-dir /root/adguard-train/reports
