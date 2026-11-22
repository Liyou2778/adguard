#!/usr/bin/env bash
# M5 local-chain benchmark: vLLM OpenAI server + bench client at several concurrencies.
# Usage: bash run_vllm_bench.sh <model_dir> [label] [port]
set -uo pipefail
MODEL_DIR="${1:-/root/models/adguard-judge-3b-merged}"
LABEL="${2:-vllm-local}"
PORT="${3:-8100}"
OUT_DIR=/root/adguard-train/reports/m5
mkdir -p "$OUT_DIR"
source /root/adguard-infer/.venv/bin/activate

echo "=== starting vLLM: model=$MODEL_DIR port=$PORT $(date) ==="
python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_DIR" \
  --served-model-name adguard-judge \
  --port "$PORT" \
  --max-model-len 2048 \
  --gpu-memory-utilization 0.85 \
  --dtype float16 \
  --disable-log-requests > "$OUT_DIR/vllm-server.log" 2>&1 &
SERVER_PID=$!
echo "server pid $SERVER_PID"

for i in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "server ready after ${i}s"
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "SERVER_DIED"
    tail -30 "$OUT_DIR/vllm-server.log"
    exit 1
  fi
  sleep 1
done

for CONCURRENCY in 1 4 8 16; do
  echo "=== benchmark vLLM concurrency=$CONCURRENCY $(date) ==="
  python /root/adguard-train/bench/bench_client.py \
    --base-url "http://127.0.0.1:${PORT}" --label "$LABEL" \
    --concurrency "$CONCURRENCY" --requests $((CONCURRENCY * 12)) \
    --output "$OUT_DIR/bench-${LABEL}-c${CONCURRENCY}.json" 2>&1 | tail -25
  echo "concurrency_${CONCURRENCY}_exit=$?"
done

kill "$SERVER_PID" 2>/dev/null || true
sleep 2
echo "=== vLLM bench done $(date) ==="
