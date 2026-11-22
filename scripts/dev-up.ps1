# AdGuard M1 - start local middleware (MySQL 8 / Redis 7 / MinIO)
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\dev-up.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Write-Host '[dev-up] starting compose stack (mysql:3307 redis:6379 minio:9000/9001) ...'
docker compose -f deploy/docker-compose.yml up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

docker compose -f deploy/docker-compose.yml ps
Write-Host ''
Write-Host '[dev-up] stack is healthy.'
Write-Host '  MySQL    : 127.0.0.1:3307  db=adguard user=adguard password=adguard'
Write-Host '  Redis    : 127.0.0.1:6379'
Write-Host '  MinIO    : 127.0.0.1:9000  console=http://127.0.0.1:9001  (minioadmin/minioadmin)'
Write-Host '  Bucket   : adguard-assets (created by minio-init)'
Write-Host ''
Write-Host 'Next: run scripts\run-control-plane.ps1 then scripts\run-gateway.ps1'
