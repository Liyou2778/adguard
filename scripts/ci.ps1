# AdGuard - local CI (same checks as .github/workflows/ci.yml, runnable offline)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\ci.ps1          # fast: go + java unit + python + compose config
#   powershell -ExecutionPolicy Bypass -File .\scripts\ci.ps1 -Full    # also Testcontainers IT (needs Docker/MySQL image)

param([switch]$Full)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$JdkHome = Join-Path $env:USERPROFILE 'tools\jdk-21'
$MavenHome = Join-Path $env:USERPROFILE 'tools\apache-maven-3.9.16'
$env:JAVA_HOME = $JdkHome
$env:PATH = "$JdkHome\bin;$MavenHome\bin;$env:PATH"

$PipelineDir = Join-Path $repo 'services\pipeline-py'
$Python = Join-Path $PipelineDir '.venv\Scripts\python.exe'

$steps = @()
function Run-Step([string]$Name, [scriptblock]$Action) {
    Write-Host ''
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit $LASTEXITCODE)" }
    $script:steps += [pscustomobject]@{ Step = $Name; Result = 'OK' }
}

Set-Location $repo

# ---- Go gateway ----
Run-Step 'go vet' { Set-Location $repo\services\gateway-go; go vet ./... }
Run-Step 'go test' { Set-Location $repo\services\gateway-go; go test ./... }
Run-Step 'go build' { Set-Location $repo\services\gateway-go; go build -o bin\gateway.exe ./cmd/gateway }

# ---- Python pipeline (M2) ----
if (Test-Path $Python) {
    Run-Step 'python tests' { Set-Location $PipelineDir; & $Python -m pytest }
    Run-Step 'm2 variant eval (gate >= 0.95)' {
        Set-Location $PipelineDir
        & $Python -m adguard_pipeline.dataset
        & $Python -m adguard_pipeline.evaluation
    }
    Run-Step 'm2 clause parser eval (gate >= 0.90)' {
        Set-Location $PipelineDir
        & $Python -m adguard_pipeline.clause_parser
        & $Python -m adguard_pipeline.clause_eval
    }
} else {
    Write-Host ''
    Write-Host 'WARN: pipeline venv missing (services/pipeline-py/.venv) - python checks skipped' -ForegroundColor Yellow
}

# ---- Java control plane ----
Run-Step 'java unit tests' { Set-Location $repo\services\control-plane-java; & .\mvnw.cmd -B -q test }

# ---- compose config ----
Run-Step 'compose config validation' { Set-Location $repo; docker compose -f deploy/docker-compose.yml config --quiet }

if ($Full) {
    Run-Step 'java integration tests (Testcontainers MySQL)' { Set-Location $repo\services\control-plane-java; & .\mvnw.cmd -B -q verify }
}

Write-Host ''
$steps | Format-Table -AutoSize | Out-String -Width 160 | Write-Host
Write-Host 'CI: PASS' -ForegroundColor Green
