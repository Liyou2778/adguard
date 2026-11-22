#!/usr/bin/env bash
# M4: one-time WSL2 training environment setup (CUDA torch + PEFT/TRL + Qwen2.5-3B-Instruct)
# Usage: wsl -d Ubuntu -u root -- bash /mnt/c/Users/Liyou/Documents/deepseek/adguard/services/model-training/scripts/setup_wsl_train.sh
set -euo pipefail

BASE=/root/adguard-train
MODELS=/root/models
mkdir -p "$BASE" "$MODELS"
cd "$BASE"

echo "[1/5] apt packages"
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq python3-venv python3-pip >/dev/null 2>&1 || true

echo "[2/5] venv"
if [ ! -x "$BASE/.venv/bin/python" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip

echo "[3/5] pip installs (torch CUDA 12.x + training stack)"
if ! python -c "import torch" >/dev/null 2>&1; then
    pip install --no-cache-dir "torch==2.5.1" \
        -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
fi
pip install --no-cache-dir \
    "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" "datasets==3.1.0" \
    "accelerate==1.1.1" "bitsandbytes==0.44.1" "sentencepiece" "protobuf" "huggingface_hub>=0.36" \
    -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

echo "[4/5] torch check"
python - <<'PY'
import torch
print("torch", torch.__version__, "cuda_available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0), round(torch.cuda.get_device_properties(0).total_memory/1024**3, 2), "GiB")
PY

echo "[5/5] download Qwen2.5-3B-Instruct (HF mirror, ~6GB)"
if [ ! -f "$MODELS/qwen2.5-3b-instruct/config.json" ]; then
    HF_ENDPOINT=https://hf-mirror.com python - <<'PY'
from huggingface_hub import snapshot_download
path = snapshot_download(
    "Qwen/Qwen2.5-3B-Instruct",
    local_dir="/root/models/qwen2.5-3b-instruct",
    allow_patterns=["*.json", "*.safetensors", "*.model", "tokenizer*"],
    ignore_patterns=["*.bin", "*.pt", "*.gguf"],
)
print("model dir:", path)
PY
fi
du -sh "$MODELS/qwen2.5-3b-instruct" 2>/dev/null || true
echo "SETUP_DONE"
