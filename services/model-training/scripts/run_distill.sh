#!/usr/bin/env bash
# M4 local distillation pipeline validation: SFT-3B teacher -> Qwen2.5-0.5B student.
set -uo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json

SFT_ADAPTER=/root/adguard-train/reports/experiments/sft-qwen25-3b-qlora-v1/artifacts/final
STUDENT_MODEL=/root/models/qwen2.5-0.5b-instruct

echo "--- generate teacher labels ---"
.venv/bin/python -m adguard_training.distill generate \
  --teacher-model-id /root/models/qwen2.5-3b-instruct \
  --teacher-adapter "$SFT_ADAPTER" \
  --data-dir /root/adguard-train/data/generated \
  --out-dir /root/adguard-train/data/distill \
  --limit 400 --max-new-tokens 256 || exit 1

echo "--- train student ---"
.venv/bin/python - <<'PY'
import json
cfg = json.loads(open("configs/distill_qwen05_student.json").read())
cfg["model_id"] = "/root/models/qwen2.5-0.5b-instruct"
json.dump(cfg, open("/tmp/distill_student_local.json", "w"), ensure_ascii=False, indent=2)
print("student config written")
PY
.venv/bin/python -m adguard_training.train_sft \
  --config /tmp/distill_student_local.json \
  --data-dir /root/adguard-train/data/distill \
  --reports-dir /root/adguard-train/reports || exit 1

echo "--- eval student ---"
bash run_eval.sh /root/adguard-train/reports/experiments/distill-qwen05-student-v1/artifacts/final \
  eval eval-distill "$STUDENT_MODEL" 2>&1 | tail -50
