#!/usr/bin/env bash
# M5 local-chain validation: serve Qwen2.5-0.5B with vLLM and benchmark the OpenAI API.
# Waits until no training/eval process is running (GPU free).
set -uo pipefail
cd /root/adguard-train
OUT=/root/adguard-train/reports/m5
mkdir -p "$OUT"
LOG=/root/adguard-train/m5_local.log
exec >> "$LOG" 2>&1
echo "=== M5 local vLLM validation wait $(date) ==="

for i in $(seq 1 240); do
  busy=$(pgrep -f "adguard_training" | wc -l)
  if [ "$busy" -eq 0 ]; then break; fi
  sleep 15
done
echo "=== GPU free $(date), starting vLLM ==="

source /root/adguard-infer/.venv/bin/activate
python - <<'PY'
import torch, vllm
print("vllm", vllm.__version__, "torch", torch.__version__, "cuda", torch.cuda.is_available())
PY

MODEL=/root/models/qwen2.5-0.5b-instruct
python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name adguard-judge \
  --port 8100 --max-model-len 2048 --gpu-memory-utilization 0.55 \
  --dtype float16 --disable-log-requests > "$OUT/vllm-server.log" 2>&1 &
SERVER_PID=$!
echo "vllm pid=$SERVER_PID"

READY=0
for i in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then READY=1; echo "server ready after ${i}s"; break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then echo "SERVER_DIED"; tail -20 "$OUT/vllm-server.log"; break; fi
  sleep 1
done

if [ "$READY" -eq 1 ]; then
  for CONCURRENCY in 1 4 8 16; do
    echo "=== bench concurrency=$CONCURRENCY $(date) ==="
    /root/adguard-infer/.venv/bin/python /root/adguard-train/bench/bench_client.py \
      --base-url http://127.0.0.1:8100 --label vllm-local-05b \
      --concurrency "$CONCURRENCY" --requests $((CONCURRENCY * 12)) \
      --max-tokens 160 \
      --output "$OUT/bench-vllm-local-05b-c${CONCURRENCY}.json"
    echo "bench_c${CONCURRENCY}_exit=$?"
  done
fi

kill "$SERVER_PID" 2>/dev/null || true
sleep 2
echo "=== M5 local vLLM validation done $(date) ==="
