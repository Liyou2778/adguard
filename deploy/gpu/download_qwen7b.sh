#!/usr/bin/env bash
# Download Qwen2.5-7B-Instruct from ModelScope (fast in CN) to $1.
set -uo pipefail
TARGET="${1:-/root/adguard-m5/models/qwen2.5-7b-instruct}"
BASE="https://modelscope.cn/models/Qwen/Qwen2.5-7B-Instruct/resolve/master"
mkdir -p "$TARGET"
cd "$TARGET"
FILES=(
  config.json
  generation_config.json
  model.safetensors.index.json
  model-00001-of-00004.safetensors
  model-00002-of-00004.safetensors
  model-00003-of-00004.safetensors
  model-00004-of-00004.safetensors
  tokenizer.json
  tokenizer_config.json
  vocab.json
  merges.txt
)
for f in "${FILES[@]}"; do
  if [ -s "$f" ]; then echo "[skip] $f ($(du -h "$f" | cut -f1))"; continue; fi
  echo "[get ] $f"
  curl -fL --retry 5 --retry-delay 3 --continue-at - --max-time 3600 -o "$f" "$BASE/$f" || { echo "FAILED $f"; exit 1; }
  echo "[ok  ] $f $(du -h "$f" | cut -f1)"
done
du -sh "$TARGET"
echo "MODEL_DOWNLOAD_DONE"
