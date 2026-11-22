# AdGuard (P2) - session environment bootstrap
# Usage:  . .\scripts\env.ps1      (dot-source: must be dot-sourced, not executed)
#
# Why this file exists:
#   This machine has a machine-wide PATH entry pointing at JDK 25
#   (C:\Program Files\Eclipse Adoptium\jdk-25...\bin) which wins over the
#   user-level PATH, so a plain `java -version` still reports 25.
#   P2 requires JDK 21 (Spring Boot 3 compatibility).
#   This script pins JDK 21 + Maven 3.9.16 for the current PowerShell session
#   without touching machine-wide settings (which require Administrator).
#
# Tool locations (installed under %USERPROFILE%\tools, see docs/M0-环境就绪报告.md):
#   JDK   : %USERPROFILE%\tools\jdk-21                    (Microsoft Build of OpenJDK 21.0.13.1)
#   Maven : %USERPROFILE%\tools\apache-maven-3.9.16

$ErrorActionPreference = 'Stop'

$JdkHome = Join-Path $env:USERPROFILE 'tools\jdk-21'
$MavenHome = Join-Path $env:USERPROFILE 'tools\apache-maven-3.9.16'

if (-not (Test-Path (Join-Path $JdkHome 'bin\java.exe'))) {
    throw "JDK 21 not found at $JdkHome. See docs/M0-环境就绪报告.md"
}
if (-not (Test-Path (Join-Path $MavenHome 'bin\mvn.cmd'))) {
    throw "Maven not found at $MavenHome. See docs/M0-环境就绪报告.md"
}

$env:JAVA_HOME = $JdkHome
$env:MAVEN_HOME = $MavenHome
$env:PATH = "$JdkHome\bin;$MavenHome\bin;$env:PATH"

Write-Host "[env] JAVA_HOME  = $env:JAVA_HOME"
Write-Host "[env] MAVEN_HOME = $env:MAVEN_HOME"
& java -version 2>&1 | ForEach-Object { Write-Host "[env] $_" }
