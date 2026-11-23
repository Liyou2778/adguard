#!/usr/bin/env bash
# Cloud stage 2: 7B QLoRA SFT on the full 1242-sample set.
set -uo pipefail
BASE=/root/adguard-m5
LOG=$BASE/logs/stage2_sft.log
exec >> "$LOG" 2>&1
echo "=== stage2 SFT start $(date) ==="
source "$BASE/venv/bin/activate"
export PYTHONPATH="$BASE/repo/services/model-training/src"
export ADGUARD_PARSED_RULES="$BASE/repo/services/pipeline-py/data/parsers/parsed_m2.json"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME="$BASE/hf-cache"

python - "$BASE/models/qwen2.5-7b-instruct" > /tmp/sft7b_config.json <<'PY'
import json, sys
cfg = {
  "name": "sft-qwen25-7b-qlora-cloud-v1",
  "model_id": sys.argv[1],
  "lora": {"r": 16, "alpha": 32, "dropout": 0.05,
           "target_modules": ["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"]},
  "train": {"batch_size": 1, "grad_accum": 8, "epochs": 1, "learning_rate": 0.0001,
            "lr_scheduler": "cosine", "warmup_ratio": 0.03, "logging_steps": 10,
            "save_steps": 100, "max_seq_length": 1024, "eval_strategy": "no", "seed": 20261123},
  "notes": "M5 cloud 7B full SFT"
}
json.dump(cfg, open("/dev/stdout", "w"), ensure_ascii=False, indent=2)
PY
cat /tmp/sft7b_config.json
python -m adguard_training.train_sft \
  --config /tmp/sft7b_config.json \
  --data-dir "$BASE/repo/services/model-training/data/generated" \
  --reports-dir "$BASE/reports"
echo "SFT_EXIT=$?"
ls -la "$BASE/reports/experiments/" 2>/dev/null | tail -5
echo "STAGE2_DONE $(date)"
