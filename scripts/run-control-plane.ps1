# AdGuard M1 - run the Java control plane on :8081
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\run-control-plane.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$JdkHome = Join-Path $env:USERPROFILE 'tools\jdk-21'
$MavenHome = Join-Path $env:USERPROFILE 'tools\apache-maven-3.9.16'
if (-not (Test-Path (Join-Path $JdkHome 'bin\java.exe'))) { throw "JDK 21 not found at $JdkHome" }
$env:JAVA_HOME = $JdkHome
$env:PATH = "$JdkHome\bin;$MavenHome\bin;$env:PATH"

Set-Location 'services\control-plane-java'
$jar = Get-ChildItem -Path 'target' -Filter 'control-plane-*-SNAPSHOT.jar' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike '*.original' } | Select-Object -First 1
if (-not $jar) {
    Write-Host '[run-control-plane] building jar (first run downloads dependencies) ...'
    & .\mvnw.cmd -B -q -DskipTests package
    if ($LASTEXITCODE -ne 0) { throw 'maven package failed' }
    $jar = Get-ChildItem -Path 'target' -Filter 'control-plane-*-SNAPSHOT.jar' |
        Where-Object { $_.Name -notlike '*.original' } | Select-Object -First 1
}
Write-Host "[run-control-plane] starting $($jar.Name) on :8081 ..."
& "$JdkHome\bin\java.exe" -jar $jar.FullName
