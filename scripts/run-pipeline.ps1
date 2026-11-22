# AdGuard M3 - run the Python compliance pipeline on :8090
# (M2 normalization/matching + M3 clause retrieval + rule judging)
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\run-pipeline.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo\services\pipeline-py

$python = Join-Path $repo 'services\pipeline-py\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "pipeline venv missing; see services\pipeline-py\README.md (python -m venv .venv + pip install)"
}

if (-not $env:ADGUARD_EMBEDDER) { $env:ADGUARD_EMBEDDER = 'auto' }
if (-not $env:ADGUARD_CLAUSE_SOURCE) { $env:ADGUARD_CLAUSE_SOURCE = 'json' }
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = 'https://hf-mirror.com' }

Write-Host "[run-pipeline] embedding=$env:ADGUARD_EMBEDDER clause-source=$env:ADGUARD_CLAUSE_SOURCE"
Write-Host '[run-pipeline] starting pipeline on :8090 (first /v1/index/status builds the clause index) ...'
& $python -m uvicorn adguard_pipeline.api:app --host 127.0.0.1 --port 8090
