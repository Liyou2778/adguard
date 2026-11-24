# AdGuard P2 - ECS deployment bundle packaging (run from repo root or via deploy-all.ps1).
# Builds a linux/amd64 gateway binary and stages gateway + control-plane.jar + pipeline source.
param(
    [string]$Version = (Get-Date -Format "yyyyMMddHHmmss"),
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$stage = Join-Path $repo "dist\ecs\stage-$Version"
$tarball = Join-Path $repo "dist\ecs\adguard-bundle-$Version.tar.gz"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stage | Out-Null

if (-not $SkipBuild) {
    Write-Host "[build] gateway linux/amd64 $Version"
    Push-Location (Join-Path $repo "services\gateway-go")
    $env:CGO_ENABLED = "0"; $env:GOOS = "linux"; $env:GOARCH = "amd64"
    & go build -trimpath -ldflags "-s -w -X main.version=$Version" -o (Join-Path $stage "gateway") ./cmd/gateway
    if ($LASTEXITCODE -ne 0) { throw "go build failed" }
    $env:GOOS = ""; $env:GOARCH = ""; $env:CGO_ENABLED = ""
    Pop-Location
} elseif (-not (Test-Path (Join-Path $stage "gateway"))) {
    throw "SkipBuild set but $stage\gateway missing"
}

$jar = Get-ChildItem (Join-Path $repo "services\control-plane-java\target") -Filter "control-plane-*.jar" |
    Where-Object { $_.Name -notlike "*.original" } | Select-Object -First 1
if (-not $jar) { throw "control-plane jar not found; run mvn package first" }
Copy-Item $jar.FullName (Join-Path $stage "control-plane.jar")

Write-Host "[package] pipeline source"
$pipelineSrc = Join-Path $repo "services\pipeline-py\src"
Copy-Item -Recurse $pipelineSrc (Join-Path $stage "pipeline-src")
Copy-Item (Join-Path $repo "services\pipeline-py\pyproject.toml") (Join-Path $stage "pipeline-pyproject.toml")
Copy-Item (Join-Path $repo "deploy\ecs\pipeline-requirements.txt") (Join-Path $stage "pipeline-requirements.txt")

Write-Host "[package] systemd units + deploy scripts"
Copy-Item -Recurse (Join-Path $repo "deploy\ecs") (Join-Path $stage "deploy")
Remove-Item -Recurse -Force (Join-Path $stage "deploy\deploy-gateway.ps1") -ErrorAction SilentlyContinue

Write-Host "[package] tarball $tarball"
Push-Location $stage
& tar -czf $tarball .
Pop-Location
Write-Host ("[done] {0} ({1:N1} MB)" -f $tarball, ((Get-Item $tarball).Length / 1MB))
