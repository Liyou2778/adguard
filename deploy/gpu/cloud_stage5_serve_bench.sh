#!/usr/bin/env bash
# Cloud stage 5: vLLM serving + benchmark + full served-API gate for 7B (SFT and DPO merges).
set -uo pipefail
BASE=/root/adguard-m5
LOG=$BASE/logs/stage5_serve_bench.log
OUT=$BASE/reports/m5
mkdir -p "$OUT"
exec >> "$LOG" 2>&1
echo "=== stage5 serve+bench start $(date) ==="
set -x
if [ ! -x "$BASE/venv-infer/bin/python" ]; then python3 -m venv "$BASE/venv-infer"; fi
source "$BASE/venv-infer/bin/activate"
INDEX="-i https://repo.huaweicloud.com/repository/pypi/simple --trusted-host repo.huaweicloud.com"
pip install --no-cache-dir --progress-bar off $INDEX "vllm==0.6.3.post1"
pip install --no-cache-dir --progress-bar off $INDEX \
  "transformers==4.46.3" "tokenizers==0.20.4" "datasets==3.1.0" "pyarrow==16.1.0" \
  "huggingface_hub==0.36.2" "httpx" "numpy==1.26.4"
# local stub for the squatted pyairports package (outlines only needs AIRPORT_LIST)
SP=$("$BASE/venv-infer/bin/python" -c 'import site; print(site.getsitepackages()[0])')
mkdir -p "$SP/pyairports"
echo '"""stub: outlines imports pyairports.airports for airport validation; unused here."""' > "$SP/pyairports/__init__.py"
echo 'AIRPORT_LIST = []' > "$SP/pyairports/airports.py"
set +x
"$BASE/venv-infer/bin/python" - <<'PY'
import torch, vllm
print("vllm", vllm.__version__, "torch", torch.__version__, "cuda", torch.cuda.is_available())
PY

serve_and_bench() {
  local model="$1" label="$2"
  [ -d "$model" ] || { echo "MODEL_MISSING $model"; return 1; }
  find_root_pid() { pgrep -f 'vllm.entrypoints.openai.api_server' | head -1; }
  local old; old=$(find_root_pid || true); [ -n "$old" ] && kill -9 "$old" 2>/dev/null || true
  sleep 2
  "$BASE/venv-infer/bin/python" -m vllm.entrypoints.openai.api_server \
    --model "$model" --served-model-name adguard-judge --port 8100 \
    --max-model-len 2048 --gpu-memory-utilization 0.90 --dtype float16 \
    --disable-log-requests > "$OUT/vllm-server-$label.log" 2>&1 &
  local pid=$!
  local ready=0
  for i in $(seq 1 300); do
    if curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then ready=1; echo "server ready after ${i}s ($label)"; break; fi
    if ! kill -0 "$pid" 2>/dev/null; then echo "SERVER_DIED ($label)"; tail -30 "$OUT/vllm-server-$label.log"; return 1; fi
    sleep 1
  done
  [ "$ready" -eq 1 ] || { echo "SERVER_NOT_READY ($label)"; return 1; }
  for C in 1 4 8 16 32; do
    "$BASE/venv-infer/bin/python" "$BASE/repo/services/model-training/bench/bench_client.py" \
      --base-url http://127.0.0.1:8100 --label "$label" \
      --concurrency "$C" --requests $((C * 8)) --max-tokens 160 \
      --output "$OUT/bench-$label-c$C.json"
    echo "bench_${label}_c${C}_exit=$?"
  done
  "$BASE/venv-infer/bin/python" "$BASE/repo/services/model-training/bench/evaluate_api.py" \
    --base-url http://127.0.0.1:8100 --data-dir "$BASE/repo/services/model-training/data/generated" \
    --rows 535 --concurrency 8 --max-tokens 384 \
    --output "$OUT/api-eval-$label-535.json"
  echo "api_eval_${label}_exit=$?"
  kill "$pid" 2>/dev/null || true
  sleep 3
}

serve_and_bench "$BASE/models/merged-sft-7b" "vllm-7b-sft"

DPO_ADAPTER=/root/adguard-m5/reports/experiments/dpo-qwen25-7b-qlora-cloud-v1/artifacts/final
if [ -f "$DPO_ADAPTER/adapter_config.json" ]; then
  echo "--- free SFT merged model, then merge DPO ---"
  rm -rf "$BASE/models/merged-sft-7b"
  PYTHONPATH="$BASE/repo/services/model-training/src" "$BASE/venv/bin/python" -m adguard_training.merge_adapter \
    --base "$BASE/models/qwen2.5-7b-instruct" --adapter "$DPO_ADAPTER" \
    --output "$BASE/models/merged-dpo-7b"
  serve_and_bench "$BASE/models/merged-dpo-7b" "vllm-7b-dpo"
fi
tar -czf "$BASE/m5-results-7b.tar.gz" -C "$BASE" reports 2>/dev/null || true
ls -la "$BASE/m5-results-7b.tar.gz" 2>/dev/null
echo "STAGE5_DONE $(date)"
