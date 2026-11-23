#!/usr/bin/env bash
# Serve the merged 3B judge with vLLM on the local 8GB GPU and benchmark it.
set -uo pipefail
OUT=/root/adguard-train/reports/m5
mkdir -p "$OUT"
LOG=/root/adguard-train/m5_3b.log
exec >> "$LOG" 2>&1
echo "=== M5 3B local vLLM test start $(date) ==="

for pid in $(pgrep -f 'vllm.entrypoints.openai.api_server' 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
sleep 2
source /root/adguard-infer/.venv/bin/activate

MODEL=/root/models/adguard-judge-3b-merged
python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name adguard-judge \
  --port 8100 --max-model-len 1024 --gpu-memory-utilization 0.92 \
  --dtype float16 --disable-log-requests > "$OUT/vllm-3b-server.log" 2>&1 &
SERVER_PID=$!
echo "server pid=$SERVER_PID"

READY=0
for i in $(seq 1 180); do
  if curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then READY=1; echo "server ready after ${i}s"; break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "SERVER_DIED"; tail -40 "$OUT/vllm-3b-server.log"; break
  fi
  sleep 1
done

if [ "$READY" -eq 1 ]; then
  for CONCURRENCY in 1 4 8; do
    python /root/adguard-train/bench/bench_client.py \
      --base-url http://127.0.0.1:8100 --label vllm-local-3b \
      --concurrency "$CONCURRENCY" --requests $((CONCURRENCY * 8)) \
      --max-tokens 160 \
      --output "$OUT/bench-vllm-local-3b-c${CONCURRENCY}.json"
    echo "bench_c${CONCURRENCY}_exit=$?"
  done
else
  echo "3B_SERVE_FAILED"
fi

kill "$SERVER_PID" 2>/dev/null || true
sleep 2
echo "=== M5 3B local test done $(date) ==="
