# AdGuard - local CI (same checks as .github/workflows/ci.yml, runnable offline)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\ci.ps1          # fast: go vet/test/build + java unit tests
#   powershell -ExecutionPolicy Bypass -File .\scripts\ci.ps1 -Full    # also Testcontainers IT (needs Docker)

param([switch]$Full)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$JdkHome = Join-Path $env:USERPROFILE 'tools\jdk-21'
$MavenHome = Join-Path $env:USERPROFILE 'tools\apache-maven-3.9.16'
$env:JAVA_HOME = $JdkHome
$env:PATH = "$JdkHome\bin;$MavenHome\bin;$env:PATH"

$steps = @()
function Run-Step([string]$Name, [scriptblock]$Action) {
    Write-Host ''
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit $LASTEXITCODE)" }
    $script:steps += [pscustomobject]@{ Step = $Name; Result = 'OK' }
}

Set-Location $repo

Run-Step 'go vet' { Set-Location services\gateway-go; go vet ./... }
Run-Step 'go test' { Set-Location $repo\services\gateway-go; go test ./... }
Run-Step 'go build' { Set-Location $repo\services\gateway-go; go build -o bin\gateway.exe ./cmd/gateway }
Run-Step 'java unit tests' { Set-Location $repo\services\control-plane-java; & .\mvnw.cmd -B -q test }
Run-Step 'compose config validation' { Set-Location $repo; docker compose -f deploy/docker-compose.yml config --quiet }

if ($Full) {
    Run-Step 'java integration tests (Testcontainers MySQL)' { Set-Location $repo\services\control-plane-java; & .\mvnw.cmd -B -q verify }
}

Write-Host ''
$steps | Format-Table -AutoSize | Out-String -Width 160 | Write-Host
Write-Host 'CI: PASS' -ForegroundColor Green
