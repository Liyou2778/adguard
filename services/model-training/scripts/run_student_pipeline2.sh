#!/usr/bin/env bash
# M4 distillation v2: wait for GPU -> left-padded batched teacher generation -> schema-filter ->
# train 0.5B student -> evaluate. Fixes the right-padding bug found in the first attempt.
set -uo pipefail
cd /root/adguard-train
export PYTHONPATH=/root/adguard-train/src
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HOME=/root/adguard-train/hf-cache
export ADGUARD_PARSED_RULES=/root/adguard-train/data/parsers/parsed_m2.json
LOG=/root/adguard-train/student_pipeline2.log
exec >> "$LOG" 2>&1
echo "=== student pipeline2 wait $(date) ==="

for i in $(seq 1 240); do
  if ! pgrep -f "adguard_training.evaluate" >/dev/null 2>&1; then break; fi
  sleep 15
done
echo "=== GPU free $(date), teacher generation (left padding, limit 250) ==="

SFT_ADAPTER=/root/adguard-train/reports/experiments/sft-qwen25-3b-qlora-v1/artifacts/final
.venv/bin/python -m adguard_training.distill generate \
  --teacher-model-id /root/models/qwen2.5-3b-instruct \
  --teacher-adapter "$SFT_ADAPTER" \
  --data-dir /root/adguard-train/data/generated \
  --out-dir /root/adguard-train/data/distill2 \
  --limit 250 --max-new-tokens 320 --batch-size 8
echo "DISTILL_V2_GEN_EXIT=$?"

echo "=== filter teacher outputs by schema validity ==="
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from adguard_training.schema import parse_output
src = Path('/root/adguard-train/data/distill2/sft_train.jsonl')
rows = [json.loads(l) for l in src.read_text(encoding='utf-8').splitlines() if l.strip()]
clean, dropped = [], 0
for row in rows:
    try:
        parse_output(row['output'])
        clean.append(row)
    except Exception:
        dropped += 1
src.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in clean) + '\n', encoding='utf-8')
print(f'teacher rows={len(rows)} clean={len(clean)} dropped={dropped} rate={len(clean)/max(1,len(rows)):.4f}')
PY

echo "=== train student $(date) ==="
.venv/bin/python - <<'PY'
import json
cfg = json.loads(open("configs/distill_qwen05_student.json").read())
cfg["model_id"] = "/root/models/qwen2.5-0.5b-instruct"
json.dump(cfg, open("/tmp/distill_student_local.json", "w"), ensure_ascii=False, indent=2)
print("student config written")
PY
.venv/bin/python -m adguard_training.train_sft \
  --config /tmp/distill_student_local.json \
  --data-dir /root/adguard-train/data/distill2 \
  --reports-dir /root/adguard-train/reports
echo "STUDENT_TRAIN_EXIT=$?"

echo "=== eval student $(date) ==="
bash run_eval.sh /root/adguard-train/reports/experiments/distill-qwen05-student-v1/artifacts/final \
  eval eval-distill /root/models/qwen2.5-0.5b-instruct 2>&1 | tail -45
echo "STUDENT_EVAL_EXIT=${PIPESTATUS[0]}"
echo "=== student pipeline2 done $(date) ==="
