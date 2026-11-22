#!/usr/bin/env bash
# M5 benchmark: SGLang server + bench client (same workload as run_vllm_bench.sh).
# Usage: bash run_sglang_bench.sh <model_dir> [label] [port]
set -uo pipefail
MODEL_DIR="${1:-/root/models/adguard-judge-3b-merged}"
LABEL="${2:-sglang-local}"
PORT="${3:-8101}"
OUT_DIR=/root/adguard-train/reports/m5
mkdir -p "$OUT_DIR"
source /root/adguard-infer/.venv/bin/activate

echo "=== starting SGLang: model=$MODEL_DIR port=$PORT $(date) ==="
python -m sglang.launch_server \
  --model-path "$MODEL_DIR" \
  --port "$PORT" \
  --served-model-name adguard-judge \
  --context-length 2048 \
  --mem-fraction-static 0.85 > "$OUT_DIR/sglang-server.log" 2>&1 &
SERVER_PID=$!
echo "server pid $SERVER_PID"

for i in $(seq 1 180); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "server ready after ${i}s"
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "SERVER_DIED"
    tail -30 "$OUT_DIR/sglang-server.log"
    exit 1
  fi
  sleep 1
done

for CONCURRENCY in 1 4 8 16; do
  echo "=== benchmark SGLang concurrency=$CONCURRENCY $(date) ==="
  python /root/adguard-train/bench/bench_client.py \
    --base-url "http://127.0.0.1:${PORT}" --label "$LABEL" \
    --concurrency "$CONCURRENCY" --requests $((CONCURRENCY * 12)) \
    --output "$OUT_DIR/bench-${LABEL}-c${CONCURRENCY}.json" 2>&1 | tail -25
  echo "concurrency_${CONCURRENCY}_exit=$?"
done

kill "$SERVER_PID" 2>/dev/null || true
sleep 2
echo "=== SGLang bench done $(date) ==="
