#!/usr/bin/env bash
# AutoDL driver: stage1b (env + 7B download) -> SFT -> DPO -> merge -> serve/bench -> SGLang attempt.
set -uo pipefail
BASE=/root/adguard-m5
LOG=$BASE/logs/run_all_autodl.log
mkdir -p "$BASE/logs"
exec >> "$LOG" 2>&1
echo "=== AutoDL run_all start $(date) ==="
cd "$BASE"
bash "$BASE/repo/deploy/gpu/cloud_stage1b_autodl.sh"
echo "=== END cloud_stage1b $(date) ==="
for s in cloud_stage2_sft cloud_stage3_dpo cloud_stage4_merge cloud_stage5_serve_bench cloud_stage6_sglang; do
  echo "=== RUN $s $(date) ==="
  bash "$BASE/repo/deploy/gpu/$s.sh"
  echo "=== END $s exit=$? $(date) ==="
done
echo "=== AutoDL run_all done $(date) ==="
