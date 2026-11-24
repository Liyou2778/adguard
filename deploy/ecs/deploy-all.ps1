# AdGuard P2 - full ECS deployment orchestration (run from repo root).
# Usage:
#   powershell -File deploy/ecs/deploy-all.ps1 -EcsHost 1.2.3.4 -SshKey C:\keys\adguard.pem
#   powershell -File deploy/ecs/deploy-all.ps1 -EcsHost 1.2.3.4 -SshKey ... -Version v1 -SkipBuild
param(
    [Parameter(Mandatory = $true)][string]$EcsHost,
    [Parameter(Mandatory = $true)][string]$SshKey,
    [string]$SshUser = "root",
    [string]$Version = (Get-Date -Format "yyyyMMddHHmmss"),
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$sshOpts = @("-i", $SshKey, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10")

function Invoke-Ssh([string]$Command) {
    & ssh @sshOpts "$SshUser@$EcsHost" $Command
    if ($LASTEXITCODE -ne 0) { throw "remote command failed (exit $LASTEXITCODE): $Command" }
}
function Invoke-Scp([string]$Local, [string]$Remote) {
    & scp @sshOpts $Local "${SshUser}@${EcsHost}:${Remote}"
    if ($LASTEXITCODE -ne 0) { throw "scp failed: $Local -> $Remote" }
}

Write-Host "[1/4] package bundle"
$pkgArgs = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "package-bundle.ps1"), "-Version", $Version)
if ($SkipBuild) { $pkgArgs += "-SkipBuild" }
& powershell @pkgArgs
if ($LASTEXITCODE -ne 0) { throw "package-bundle failed" }
$tarball = Join-Path $repo "dist\ecs\adguard-bundle-$Version.tar.gz"
if (-not (Test-Path $tarball)) { throw "missing $tarball" }

Write-Host "[2/4] upload bundle + remote deploy script"
Invoke-Ssh "mkdir -p /opt/adguard/releases /opt/adguard/scripts"
Invoke-Scp $tarball "/opt/adguard/releases/adguard-bundle-$Version.tar.gz"
Invoke-Scp (Join-Path $PSScriptRoot "remote-deploy-bundle.sh") "/opt/adguard/scripts/remote-deploy-bundle.sh"
Invoke-Ssh "chmod +x /opt/adguard/scripts/remote-deploy-bundle.sh"

Write-Host "[3/4] remote bundle deploy"
Invoke-Ssh "bash /opt/adguard/scripts/remote-deploy-bundle.sh /opt/adguard/releases/adguard-bundle-$Version.tar.gz $Version"

Write-Host "[4/4] public health check"
$code = & ssh @sshOpts "$SshUser@$EcsHost" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/healthz"
if ("$code".Trim() -ne "200") { throw "public healthz failed: $code" }
Write-Host "[done] bundle version=$Version, public healthz 200"
