# AdGuard M1 - run the Go gateway on :8080
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\run-gateway.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo\services\gateway-go

# Local defaults match deploy/docker-compose.yml. Override by setting the env vars before running.
if (-not $env:GATEWAY_HTTP_ADDR)          { $env:GATEWAY_HTTP_ADDR = ':8080' }
if (-not $env:GATEWAY_MYSQL_DSN)          { $env:GATEWAY_MYSQL_DSN = 'adguard:adguard@tcp(127.0.0.1:3307)/adguard?parseTime=true&charset=utf8mb4&loc=UTC' }
if (-not $env:GATEWAY_REDIS_ADDR)         { $env:GATEWAY_REDIS_ADDR = '127.0.0.1:6379' }
if (-not $env:GATEWAY_MINIO_ENDPOINT)     { $env:GATEWAY_MINIO_ENDPOINT = '127.0.0.1:9000' }
if (-not $env:GATEWAY_MINIO_PUBLIC_ENDPOINT) { $env:GATEWAY_MINIO_PUBLIC_ENDPOINT = '127.0.0.1:9000' }
if (-not $env:GATEWAY_MINIO_ACCESS_KEY)   { $env:GATEWAY_MINIO_ACCESS_KEY = 'minioadmin' }
if (-not $env:GATEWAY_MINIO_SECRET_KEY)   { $env:GATEWAY_MINIO_SECRET_KEY = 'minioadmin' }
if (-not $env:GATEWAY_MINIO_BUCKET)       { $env:GATEWAY_MINIO_BUCKET = 'adguard-assets' }
if (-not $env:GATEWAY_CONTROL_PLANE_URL)  { $env:GATEWAY_CONTROL_PLANE_URL = 'http://127.0.0.1:8081' }

Write-Host '[run-gateway] starting Go gateway on :8080 ...'
go run ./cmd/gateway
