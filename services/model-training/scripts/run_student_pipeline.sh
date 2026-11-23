#!/usr/bin/env bash
# Wait for the running DPO re-eval to finish, then train the distilled 0.5B student and evaluate it.
set -uo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json
LOG=/root/adguard-train/student_pipeline.log
exec >> "$LOG" 2>&1
echo "=== student pipeline wait $(date) ==="

# wait until no evaluate process is running (GPU free)
for i in $(seq 1 240); do
  if ! pgrep -f "adguard_training.evaluate" >/dev/null 2>&1; then break; fi
  sleep 15
done
echo "=== GPU free, starting student training $(date) ==="

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
  --reports-dir /root/adguard-train/reports
echo "STUDENT_TRAIN_EXIT=$?"

bash run_eval.sh /root/adguard-train/reports/experiments/distill-qwen05-student-v1/artifacts/final \
  eval eval-distill /root/models/qwen2.5-0.5b-instruct 2>&1 | tail -45
echo "STUDENT_EVAL_EXIT=${PIPESTATUS[0]}"
echo "=== student pipeline done $(date) ==="
