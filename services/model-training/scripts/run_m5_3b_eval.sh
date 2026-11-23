#!/usr/bin/env bash
# Serve merged 3B judge and run: benchmark (fixed TPOT) + API quality eval (150 rows).
set -uo pipefail
OUT=/root/adguard-train/reports/m5
mkdir -p "$OUT"
LOG=/root/adguard-train/m5_3b_eval.log
exec >> "$LOG" 2>&1
echo "=== 3B serve+eval start $(date) ==="
for pid in $(pgrep -f 'vllm.entrypoints.openai.api_server' 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
sleep 2
source /root/adguard-infer/.venv/bin/activate
MODEL=/root/models/adguard-judge-3b-merged
python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name adguard-judge \
  --port 8100 --max-model-len 1024 --gpu-memory-utilization 0.92 \
  --dtype float16 --disable-log-requests > "$OUT/vllm-3b-server.log" 2>&1 &
SERVER_PID=$!
for i in $(seq 1 180); do
  if curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then echo "server ready after ${i}s"; break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then echo SERVER_DIED; tail -30 "$OUT/vllm-3b-server.log"; break; fi
  sleep 1
done
echo "--- API quality eval: 150 held-out rows, concurrency 8 ---"
python /root/adguard-train/bench/evaluate_api.py \
  --base-url http://127.0.0.1:8100 \
  --data-dir /root/adguard-train/data/generated \
  --rows 150 --concurrency 8 --max-tokens 384 \
  --output "$OUT/api-eval-3b-150.json"
echo "API_EVAL_EXIT=$?"
kill "$SERVER_PID" 2>/dev/null || true
sleep 2
echo "=== 3B serve+eval done $(date) ==="
