# P2 AdGuard - M6 gray-release deployment for the Go gateway (run from the repo root).
#
# Usage:
#   powershell -File deploy/ecs/deploy-gateway.ps1 -EcsHost 1.2.3.4 -SshKey C:\keys\adguard.pem
#   powershell -File deploy/ecs/deploy-gateway.ps1 -EcsHost 1.2.3.4 -SshKey ... -Rollback
#
# Flow: build linux binary -> tar -> scp -> remote deploy as canary (:28080)
#       -> health check -> set canary traffic 10/50/100 (each health-checked)
#       -> cutover to stable (:18080) -> nginx back to 0% canary.
param(
    [Parameter(Mandatory = $true)][string]$EcsHost,
    [Parameter(Mandatory = $true)][string]$SshKey,
    [string]$SshUser = "root",
    [string]$Version = (Get-Date -Format "yyyyMMddHHmmss"),
    [string]$CanarySteps = "10,50,100",
    [switch]$Rollback,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dist = Join-Path $repo "dist\ecs"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$tarball = Join-Path $dist "gateway-$Version.tar.gz"
$sshOpts = @("-i", $SshKey, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10")

function Invoke-Ssh([string]$Command) {
    & ssh @sshOpts "$SshUser@$EcsHost" $Command
    if ($LASTEXITCODE -ne 0) { throw "remote command failed (exit $LASTEXITCODE): $Command" }
}
function Invoke-Scp([string]$Local, [string]$Remote) {
    & scp @sshOpts $Local "${SshUser}@${EcsHost}:${Remote}"
    if ($LASTEXITCODE -ne 0) { throw "scp failed: $Local -> $Remote" }
}
function Get-RemoteHealth {
    $code = & ssh @sshOpts "$SshUser@$EcsHost" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/healthz"
    return "$code".Trim()
}

if ($Rollback) {
    Write-Host "[rollback] switching stable back to previous release ..."
    Invoke-Ssh "bash /opt/adguard/scripts/remote-cutover-or-rollback.sh rollback"
    $code = Get-RemoteHealth
    if ($code -ne "200") { throw "rollback health check failed: $code" }
    Write-Host "[rollback] PASS (healthz 200)"
    exit 0
}

if (-not $SkipBuild) {
    Write-Host "[build] go linux/amd64"
    Push-Location (Join-Path $repo "services\gateway-go")
    $env:CGO_ENABLED = "0"; $env:GOOS = "linux"; $env:GOARCH = "amd64"
    & go build -trimpath -ldflags "-s -w -X main.version=$Version" -o (Join-Path $dist "gateway") ./cmd/gateway
    if ($LASTEXITCODE -ne 0) { throw "go build failed" }
    $env:GOOS = ""; $env:GOARCH = ""; $env:CGO_ENABLED = ""
    Pop-Location
} else {
    Write-Host "[build] skipped (reusing $dist\gateway)"
}
if (-not (Test-Path (Join-Path $dist "gateway"))) { throw "missing $dist\gateway" }

Write-Host "[package] $tarball"
Push-Location $dist
& tar -czf $tarball gateway
Pop-Location

Write-Host "[upload] $tarball -> ECS"
Invoke-Scp $tarball "/tmp/gateway-$Version.tar.gz"
Invoke-Ssh "mkdir -p /opt/adguard/scripts && cp /tmp/gateway-$Version.tar.gz /opt/adguard/releases/"

# remote helper scripts (idempotent copy each deploy)
$helpers = @("remote-deploy-gateway.sh", "remote-cutover-or-rollback.sh", "switch-canary.sh")
foreach ($helper in $helpers) {
    Invoke-Scp (Join-Path $PSScriptRoot $helper) "/opt/adguard/scripts/$helper"
}
Invoke-Ssh "chmod +x /opt/adguard/scripts/*.sh"

Write-Host "[deploy] canary release $Version"
Invoke-Ssh "bash /opt/adguard/scripts/remote-deploy-gateway.sh /opt/adguard/releases/gateway-$Version.tar.gz $Version"

foreach ($percent in $CanarySteps.Split(",")) {
    Invoke-Ssh "bash /opt/adguard/scripts/switch-canary.sh $percent"
    Start-Sleep -Seconds 5
    $code = Get-RemoteHealth
    if ($code -ne "200") { throw "public healthz failed ($code) at canary ${percent}%" }
    Invoke-Ssh "curl -fsS http://127.0.0.1:8100/healthz >/dev/null 2>&1 || true" # noop guard
    Write-Host "[canary] ${percent}% -> healthz 200"
}

Write-Host "[cutover] promoting canary to stable"
Invoke-Ssh "bash /opt/adguard/scripts/remote-cutover-or-rollback.sh cutover"
Invoke-Ssh "bash /opt/adguard/scripts/switch-canary.sh 0"
$code = Get-RemoteHealth
if ($code -ne "200") { throw "post-cutover health check failed: $code" }
Write-Host "[done] version=$Version, public healthz 200, canary traffic 0%"
