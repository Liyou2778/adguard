#!/usr/bin/env bash
# M5 cloud-GPU bootstrap: run this ON the rented GPU machine (Ubuntu 22.04, CUDA 12.x).
#
#   bash bootstrap_m5.sh <workdir> <model-size:3b|7b|8b>
#
# It is intentionally idempotent and logs every stage. The training/benchmark code is the
# SAME code used locally (services/model-training); only model_id and batch sizes change.
set -uo pipefail

WORKDIR="${1:-/root/adguard-m5}"
MODEL_SIZE="${2:-7b}"
REPO_TAR="${REPO_TAR:-/root/adguard-repo.tar.gz}"

mkdir -p "$WORKDIR"
cd "$WORKDIR"
LOG="$WORKDIR/bootstrap.log"
exec >> "$LOG" 2>&1
echo "=== bootstrap start $(date) size=$MODEL_SIZE ==="

echo "=== [1/8] GPU / driver check ==="
nvidia-smi || { echo "NO_GPU"; exit 1; }
python -c "import torch, sys; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" || {
  echo "torch missing -> installing CUDA wheel (cu121)";
  pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
}

echo "=== [2/8] repo + data ==="
if [ ! -d "$WORKDIR/adguard" ]; then
  tar -xzf "$REPO_TAR" -C "$WORKDIR" || { echo "REPO_TAR_MISSING"; exit 1; }
fi
cd "$WORKDIR/adguard"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" "datasets==3.1.0" \
  "accelerate==1.1.1" "bitsandbytes==0.44.1" "sentencepiece" "protobuf" "httpx" \
  -i https://repo.huaweicloud.com/repository/pypi/simple --trusted-host repo.huaweicloud.com

echo "=== [3/8] model weights (ModelScope, fast in CN) ==="
case "$MODEL_SIZE" in
  3b) MS_ID="Qwen/Qwen2.5-3B-Instruct"; SIZE_HINT="6GB" ;;
  7b) MS_ID="Qwen/Qwen2.5-7B-Instruct"; SIZE_HINT="15GB" ;;
  8b) MS_ID="Qwen/Qwen2.5-8B-Instruct"; SIZE_HINT="16GB" ;;
  *) echo "unknown size"; exit 1 ;;
esac
MODEL_DIR="$WORKDIR/models/$(basename "$MS_ID")"
mkdir -p "$MODEL_DIR"
python - "$MS_ID" "$MODEL_DIR" <<'PY'
import sys
from huggingface_hub import snapshot_download
repo_id, local_dir = sys.argv[1], sys.argv[2]
path = snapshot_download(repo_id, local_dir=local_dir,
                         allow_patterns=["*.json", "*.safetensors", "*.model", "tokenizer*"],
                         ignore_patterns=["*.bin", "*.pt", "*.gguf"])
print("model:", path)
PY
[ "$?" -ne 0 ] && { echo "MODEL_DOWNLOAD_FAILED (fallback: curl from ModelScope, see docs/M5)"; }
du -sh "$MODEL_DIR" || true

echo "=== [4/8] quick VRAM probe ==="
python - <<'PY'
import torch
if not torch.cuda.is_available():
    raise SystemExit("CUDA not available")
free, total = torch.cuda.mem_get_info()
print(f"GPU {torch.cuda.get_device_name(0)} free={free/1024**3:.1f}GiB total={total/1024**3:.1f}GiB")
PY

echo "=== [5/8] SFT (full 1242 samples; 7B/8B QLoRA) ==="
python - "$MODEL_DIR" <<'PY' > "$WORKDIR/sft_config.json"
import json, sys
model_dir = sys.argv[1]
cfg = {
  "name": f"sft-qwen-{model_dir.split('/')[-1]}-qlora-v1",
  "model_id": model_dir,
  "lora": {"r": 16, "alpha": 32, "dropout": 0.05,
           "target_modules": ["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"]},
  "train": {"batch_size": 1, "grad_accum": 8, "epochs": 1, "learning_rate": 0.0001,
            "lr_scheduler": "cosine", "warmup_ratio": 0.03, "logging_steps": 10,
            "save_steps": 100, "max_seq_length": 1024, "eval_strategy": "no", "seed": 20261123},
  "notes": "M5 cloud full SFT"
}
json.dump(cfg, open("/dev/stdout", "w"), ensure_ascii=False, indent=2)
PY
PYTHONPATH=services/model-training/src python -m adguard_training.train_sft \
  --config "$WORKDIR/sft_config.json" \
  --data-dir services/model-training/data/generated \
  --reports-dir services/model-training/reports
echo "SFT_EXIT=$?"

echo "=== [6/8] merge adapter ==="
SFT_ADAPTER=$(ls -d services/model-training/reports/experiments/sft-*/artifacts/final 2>/dev/null | tail -1)
if [ -n "$SFT_ADAPTER" ]; then
  PYTHONPATH=services/model-training/src python -m adguard_training.merge_adapter \
    --base "$MODEL_DIR" --adapter "$SFT_ADAPTER" --output "$WORKDIR/models/merged"
fi
echo "MERGE_DONE"

echo "=== [7/8] benchmark (vLLM; SGLang optional) ==="
pip install -q vllm==0.6.3.post1 httpx -i https://repo.huaweicloud.com/repository/pypi/simple \
  --trusted-host repo.huaweicloud.com || echo "VLLM_INSTALL_FAILED"
mkdir -p services/model-training/reports/m5
if [ -d "$WORKDIR/models/merged" ]; then
  bash services/model-training/scripts/run_vllm_bench.sh "$WORKDIR/models/merged" "vllm-cloud-$MODEL_SIZE"
  bash services/model-training/bench/evaluate_api.py \
    --base-url http://127.0.0.1:8100 --data-dir services/model-training/data/generated \
    --rows 535 --concurrency 8 --max-tokens 384 \
    --output "services/model-training/reports/m5/api-eval-$MODEL_SIZE-535.json" || true
fi

echo "=== [8/8] collect results ==="
tar -czf "$WORKDIR/m5-results-$MODEL_SIZE.tar.gz" services/model-training/reports 2>/dev/null || true
echo "=== bootstrap done $(date) ==="
echo "RESULTS: $WORKDIR/m5-results-$MODEL_SIZE.tar.gz"
