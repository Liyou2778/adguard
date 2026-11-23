#!/usr/bin/env bash
# Cloud pipeline driver: runs stage1..stage6 sequentially, each stage self-logs.
set -uo pipefail
BASE=/root/adguard-m5
LOG=$BASE/logs/run_all.log
mkdir -p "$BASE/logs"
exec >> "$LOG" 2>&1
echo "=== cloud run_all start $(date) ==="
cd "$BASE"
for stage in cloud_stage1_setup cloud_stage2_sft cloud_stage3_dpo cloud_stage4_merge cloud_stage5_serve_bench cloud_stage6_sglang; do
  echo "=== RUN $stage $(date) ==="
  bash "$BASE/repo/deploy/gpu/$stage.sh"
  echo "=== END $stage exit=$? $(date) ==="
done
echo "=== cloud run_all done $(date) ==="
