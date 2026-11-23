#!/usr/bin/env bash
# Cloud stage 3: DPO on top of the 7B SFT adapter (EOS-fixed completions).
set -uo pipefail
BASE=/root/adguard-m5
LOG=$BASE/logs/stage3_dpo.log
exec >> "$LOG" 2>&1
echo "=== stage3 DPO start $(date) ==="
source "$BASE/venv/bin/activate"
export PYTHONPATH="$BASE/repo/services/model-training/src"
export ADGUARD_PARSED_RULES="$BASE/repo/services/pipeline-py/data/parsers/parsed_m2.json"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

SFT_ADAPTER=$(ls -d "$BASE"/reports/experiments/sft-qwen25-7b-qlora-cloud-v1/artifacts/final 2>/dev/null)
if [ -z "$SFT_ADAPTER" ] || [ ! -f "$SFT_ADAPTER/adapter_config.json" ]; then
  echo "SFT_ADAPTER_MISSING"; exit 1
fi
python - "$BASE/models/qwen2.5-7b-instruct" > /tmp/dpo7b_config.json <<'PY'
import json, sys
cfg = {
  "name": "dpo-qwen25-7b-qlora-cloud-v1",
  "model_id": sys.argv[1],
  "train": {"batch_size": 1, "grad_accum": 8, "epochs": 1, "learning_rate": 0.00005,
            "warmup_ratio": 0.05, "save_steps": 100, "beta": 0.1,
            "max_length": 1024, "max_prompt_length": 768, "seed": 20261123},
  "notes": "M5 cloud DPO full 1242 pairs with EOS-fixed completions"
}
json.dump(cfg, open("/dev/stdout", "w"), ensure_ascii=False, indent=2)
PY
cat /tmp/dpo7b_config.json
python -m adguard_training.train_dpo \
  --config /tmp/dpo7b_config.json \
  --data-dir "$BASE/repo/services/model-training/data/generated" \
  --sft-adapter "$SFT_ADAPTER" \
  --reports-dir "$BASE/reports"
echo "DPO_EXIT=$?"
echo "STAGE3_DONE $(date)"
