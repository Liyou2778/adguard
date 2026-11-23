#!/usr/bin/env bash
# M5 local 3B evidence: serve merged SFT-3B with vLLM, benchmark (fixed TPOT), full 535-row API gate.
set -uo pipefail
OUT=/root/adguard-train/reports/m5
mkdir -p "$OUT"
LOG=/root/adguard-train/m5_3b_full.log
exec >> "$LOG" 2>&1
echo "=== 3B full serve+eval start $(date) ==="
for pid in $(pgrep -f 'vllm.entrypoints.openai.api_server' 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
sleep 2
source /root/adguard-infer/.venv/bin/activate
MODEL=/root/models/adguard-judge-3b-merged
python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name adguard-judge \
  --port 8100 --max-model-len 1024 --gpu-memory-utilization 0.92 \
  --dtype float16 --disable-log-requests > "$OUT/vllm-3b-server.log" 2>&1 &
SERVER_PID=$!
for i in $(seq 1 240); do
  if curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then echo "server ready after ${i}s"; break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then echo SERVER_DIED; tail -30 "$OUT/vllm-3b-server.log"; exit 1; fi
  sleep 1
done
for CONCURRENCY in 1 4 8; do
  echo "--- bench 3B concurrency=$CONCURRENCY $(date) ---"
  python /root/adguard-train/bench/bench_client.py \
    --base-url http://127.0.0.1:8100 --label vllm-local-3b \
    --concurrency "$CONCURRENCY" --requests $((CONCURRENCY * 8)) \
    --max-tokens 160 \
    --output "$OUT/bench-vllm-local-3b-c${CONCURRENCY}.json"
  echo "bench_c${CONCURRENCY}_exit=$?"
done
echo "--- full API gate: 535 held-out rows, concurrency 8 (384 tokens) $(date) ---"
python /root/adguard-train/bench/evaluate_api.py \
  --base-url http://127.0.0.1:8100 \
  --data-dir /root/adguard-train/data/generated \
  --rows 535 --concurrency 8 --max-tokens 384 \
  --output "$OUT/api-eval-3b-535.json"
echo "API_EVAL_FULL_EXIT=$?"
kill "$SERVER_PID" 2>/dev/null || true
sleep 2
echo "=== 3B full serve+eval done $(date) ==="
