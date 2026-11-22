# AdGuard M1 - stop local middleware
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\dev-down.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
docker compose -f deploy/docker-compose.yml down
Write-Host '[dev-down] stack stopped (volumes preserved; add -v to drop data).'
