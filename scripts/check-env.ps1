# AdGuard (P2) - M0 environment gate
# Usage:  pwsh -File .\scripts\check-env.ps1
# Exit code 0 = M0 gate passed, 1 = at least one hard check failed.
#
# Hard checks (PRD M0 acceptance):
#   docker  : docker info returns a server version
#   gpu     : nvidia-smi returns a GPU name + memory
#   jdk     : JDK 21 (JAVA_HOME or user-level JAVA_HOME)
#   go      : go >= 1.22
#   python  : python 3.12
#   disk    : >= 60 GB free on the workspace drive
#   maven   : mvn runs on JDK 21
# Soft checks (reported, do not fail the gate):
#   memory, docker compose, network mirrors

$ErrorActionPreference = 'Continue'
$results = @()

function Add-Result([string]$Name, [string]$Status, [string]$Detail) {
    $script:results += [pscustomobject]@{ Item = $Name; Status = $Status; Detail = $Detail }
}

# ---------- env pins ----------
$JdkHome = Join-Path $env:USERPROFILE 'tools\jdk-21'
$MavenHome = Join-Path $env:USERPROFILE 'tools\apache-maven-3.9.16'
$userJavaHome = [Environment]::GetEnvironmentVariable('JAVA_HOME', 'User')
if (-not $env:JAVA_HOME -or $env:JAVA_HOME -ne $JdkHome) { $env:JAVA_HOME = $JdkHome }
if (Test-Path $MavenHome) { $env:PATH = "$JdkHome\bin;$MavenHome\bin;$env:PATH" }

# ---------- 1. Docker engine ----------
$serverVersion = (& docker info --format '{{.ServerVersion}}' 2>$null)
if ($LASTEXITCODE -eq 0 -and $serverVersion) {
    $compose = (& docker compose version --short 2>$null)
    Add-Result 'Docker engine' 'PASS' "server $serverVersion, compose $compose"
} else {
    Add-Result 'Docker engine' 'FAIL' 'docker info failed - is Docker Desktop running?'
}

# ---------- 2. GPU ----------
$gpu = (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null)
if ($LASTEXITCODE -eq 0 -and $gpu) {
    Add-Result 'GPU' 'PASS' ($gpu -join '; ')
} else {
    Add-Result 'GPU' 'FAIL' 'nvidia-smi failed'
}

# ---------- 3. JDK 21 ----------
$javaOut = ('{0}' -f (& "$JdkHome\bin\java.exe" -version 2>&1 | Out-String))
if ($javaOut -match 'version "(21\.\d+)') {
    $effective = (& java -version 2>&1 | Out-String) -replace "`r?`n", ' '
    Add-Result 'JDK 21' 'PASS' "JAVA_HOME=$JdkHome, toolchain=$($Matches[0].Trim())"
} else {
    Add-Result 'JDK 21' 'FAIL' "JDK 21 not found under $JdkHome"
}

# ---------- 4. Go ----------
$goOut = (& go version 2>$null)
if ($LASTEXITCODE -eq 0 -and $goOut -match 'go(\d+)\.(\d+)' ) {
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -gt 1 -or ($major -eq 1 -and $minor -ge 22)) {
        Add-Result 'Go >= 1.22' 'PASS' $goOut
    } else {
        Add-Result 'Go >= 1.22' 'FAIL' $goOut
    }
} else {
    Add-Result 'Go >= 1.22' 'FAIL' 'go not found'
}

# ---------- 5. Python ----------
$pyOut = (& python --version 2>&1 | Out-String).Trim()
if ($pyOut -match 'Python 3\.12') {
    Add-Result 'Python 3.12' 'PASS' $pyOut
} else {
    Add-Result 'Python 3.12' 'WARN' $pyOut
}

# ---------- 6. Disk ----------
$free = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
if ($free -ge 60) {
    Add-Result 'Disk free (C:)' 'PASS' "$free GB free (need >= 60 GB)"
} else {
    Add-Result 'Disk free (C:)' 'FAIL' "$free GB free (need >= 60 GB)"
}

# ---------- 7. Maven on JDK 21 ----------
$mvnOut = (& "$MavenHome\bin\mvn.cmd" -v 2>&1 | Out-String)
if ($mvnOut -match 'Apache Maven' -and $mvnOut -match 'Java version: 21') {
    $mvnVer = ([regex]::Match($mvnOut, 'Apache Maven ([\d\.]+)')).Groups[1].Value
    Add-Result 'Maven' 'PASS' "mvn $mvnVer on JDK 21 (aliyun mirror in ~/.m2/settings.xml)"
} else {
    Add-Result 'Maven' 'FAIL' 'mvn -v did not report Java 21'
}

# ---------- soft checks ----------
$ram = [math]::Round((Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize / 1MB, 1)
Add-Result 'Memory' 'INFO' "$ram GB (WSL capped at 6 GB in .wslconfig)"
Add-Result 'User JAVA_HOME' 'INFO' "$userJavaHome"
$sysJava = ('{0}' -f (& java -version 2>&1 | Out-String)).Trim() -replace "`r?`n", ' '
Add-Result 'Shell default java' 'INFO' $sysJava

# ---------- report ----------
Write-Host ''
Write-Host '================= AdGuard M0 environment gate ================='
$results | Format-Table -AutoSize | Out-String -Width 160 | Write-Host
$hardFail = @($results | Where-Object { $_.Status -eq 'FAIL' })
if ($hardFail.Count -eq 0) {
    Write-Host 'M0 GATE: PASS' -ForegroundColor Green
    exit 0
} else {
    Write-Host ("M0 GATE: FAIL ({0} item(s))" -f $hardFail.Count) -ForegroundColor Red
    exit 1
}
