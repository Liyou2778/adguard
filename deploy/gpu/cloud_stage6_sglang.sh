#!/usr/bin/env bash
# Cloud stage 6: attempt SGLang install + benchmark on the merged 7B model.
# Timeboxed and non-fatal: if SGLang cannot be installed/run on this image, record it honestly.
set -uo pipefail
BASE=/root/adguard-m5
LOG=$BASE/logs/stage6_sglang.log
OUT=$BASE/reports/m5
mkdir -p "$OUT"
exec >> "$LOG" 2>&1
echo "=== stage6 SGLang attempt start $(date) ==="
if [ ! -d "$BASE/models/merged-sft-7b" ]; then echo "NO_MERGED_MODEL"; exit 0; fi

python3 -m venv "$BASE/venv-sglang" 2>/dev/null || true
source "$BASE/venv-sglang/bin/activate"
INDEX="-i https://repo.huaweicloud.com/repository/pypi/simple --trusted-host repo.huaweicloud.com"
echo "--- installing sglang (timebox 25 min) ---"
timeout 1500 pip install --no-cache-dir --progress-bar off $INDEX "sglang[all]==0.3.6.post1" \
  "torch==2.4.0" "transformers==4.46.3" "tokenizers==0.20.4" "httpx" 2>&1 | tail -20
echo "SGLANG_INSTALL_EXIT=$?"
if ! "$BASE/venv-sglang/bin/python" -c "import sglang" >/dev/null 2>&1; then
  echo "SGLANG_IMPORT_FAILED"; exit 0
fi

"$BASE/venv-sglang/bin/python" -m sglang.launch_server \
  --model-path "$BASE/models/merged-sft-7b" --port 8101 \
  --context-length 2048 --mem-fraction-static 0.85 > "$OUT/sglang-server.log" 2>&1 &
PID=$!
READY=0
for i in $(seq 1 300); do
  if curl -fsS http://127.0.0.1:8101/health >/dev/null 2>&1; then READY=1; echo "sglang ready after ${i}s"; break; fi
  if ! kill -0 "$PID" 2>/dev/null; then echo "SGLANG_DIED"; tail -40 "$OUT/sglang-server.log"; break; fi
  sleep 1
done
if [ "$READY" -eq 1 ]; then
  for C in 1 4 8 16; do
    "$BASE/venv-sglang/bin/python" "$BASE/repo/services/model-training/bench/bench_client.py" \
      --base-url http://127.0.0.1:8101 --label sglang-7b-sft \
      --concurrency "$C" --requests $((C * 8)) --max-tokens 160 \
      --output "$OUT/bench-sglang-7b-sft-c$C.json"
    echo "sglang_bench_c${C}_exit=$?"
  done
fi
kill "$PID" 2>/dev/null || true
tar -czf "$BASE/m5-results-sglang.tar.gz" -C "$BASE" reports 2>/dev/null || true
echo "STAGE6_DONE $(date)"
