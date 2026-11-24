#!/usr/bin/env bash
# Cloud stage 1 for AutoDL (torch preinstalled in the image): system-site venv + 7B download.
set -uo pipefail
BASE=/root/adguard-m5
mkdir -p "$BASE"/{logs,models,repo,reports}
LOG=$BASE/logs/stage1b.log
exec >> "$LOG" 2>&1
echo "=== stage1b autodl start $(date) ==="
set -x
# inherit the image's torch via --system-site-packages; venv isolates our extra pip packages
python3 -m venv --system-site-packages "$BASE/venv"
source "$BASE/venv/bin/activate"
python -c "import torch; print('base torch', torch.__version__, 'cuda', torch.cuda.is_available())"
pip install -q --upgrade pip
INDEX="-i https://repo.huaweicloud.com/repository/pypi/simple --trusted-host repo.huaweicloud.com"
pip install --no-cache-dir --progress-bar off $INDEX \
  "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" "datasets==3.1.0" \
  "accelerate==1.1.1" "bitsandbytes==0.44.1" "sentencepiece" "protobuf" "httpx" \
  "numpy==1.26.4" "pyarrow==16.1.0"
set +x
python - <<'PY'
import torch, transformers, peft, trl, datasets, bitsandbytes
print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
      "| transformers", transformers.__version__, "| peft", peft.__version__)
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0),
          round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2), "GiB")
PY
bash "$BASE/repo/deploy/gpu/download_qwen7b.sh" "$BASE/models/qwen2.5-7b-instruct"
echo "STAGE1B_DONE $(date)"
