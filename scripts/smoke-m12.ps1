# M12 acceptance smoke: experience enhancements
#  - landing-page render path (screenshot when Playwright enabled; dom-only on ECS by design)
#  - video ASR (faster-whisper tiny) end-to-end through the gateway
param(
    [string]$Gateway = 'http://127.0.0.1:18080',
    [string]$ControlPlane = 'http://127.0.0.1:18081',
    [string]$Pipeline = 'http://127.0.0.1:18090',
    [string]$AdminUser = 'admin',
    [string]$AdminPassword = '',
    [string]$AdminKey = '',
    [string]$ApiKey = '',
    [string]$Tenant = 'demo-platform',
    [string]$VideoPath = '',
    [switch]$ExpectScreenshot,
    [int]$PollSeconds = 60
)
$ErrorActionPreference = 'Stop'
$script:passed = 0; $script:failed = 0; $script:checks = @()
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { $script:passed++; $status = 'PASS' } else { $script:failed++; $status = 'FAIL' }
    $script:checks += [pscustomobject]@{ Check = $Name; Status = $status; Detail = $Detail }
    $color = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}  {2}" -f $status, $Name, $Detail) -ForegroundColor $color
}
function Invoke-Api([string]$Method, [string]$Url, $Body = $null, [string]$Token = '', [string]$TenantHeader = '') {
    $headers = @{}
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    if ($TenantHeader) { $headers['X-Tenant-Id'] = $TenantHeader }
    if ($AdminKey) { $headers['X-Admin-Key'] = $AdminKey }
    if ($ApiKey) { $headers['X-API-Key'] = $ApiKey }
    $resp = $null; $content = ''
    try {
        $params = @{ Uri = $Url; Method = $Method; Headers = $headers; UseBasicParsing = $true; TimeoutSec = 120 }
        if ($null -ne $Body) {
            $params['ContentType'] = 'application/json; charset=utf-8'
            $params['Body'] = [System.Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 10 -Compress))
        }
        $resp = Invoke-WebRequest @params
        $content = $resp.Content
    } catch {
        $resp = $_.Exception.Response
        if ($null -ne $_.ErrorDetails -and $_.ErrorDetails.Message) { $content = [string]$_.ErrorDetails.Message }
        elseif ($null -ne $resp) { try { $sr = New-Object System.IO.StreamReader($resp.GetResponseStream()); $content = $sr.ReadToEnd(); $sr.Close() } catch {} }
    }
    $status = 0; if ($null -ne $resp) { $status = [int]$resp.StatusCode }
    $json = $null
    if ($content) { try { $json = $content | ConvertFrom-Json } catch {} }
    return [pscustomobject]@{ Status = $status; Body = $content; Json = $json }
}
function Wait-Judged([string]$AssetId) {
    for ($i = 0; $i -lt $PollSeconds; $i++) {
        Start-Sleep -Seconds 1
        $asset = Invoke-Api GET "$Gateway/api/v1/assets/$AssetId" $null '' $Tenant
        if ($asset.Json -and $asset.Json.latest_judgment_id) {
            $judgment = Invoke-Api GET "$Gateway/api/v1/judgments/$($asset.Json.latest_judgment_id)" $null '' $Tenant
            return [pscustomobject]@{ Asset = $asset.Json; Judgment = $judgment.Json }
        }
        if ($asset.Json -and $asset.Json.status -eq 'failed') { return $null }
    }
    return $null
}
function Upload-Bytes([string]$Type, [string]$Filename, [string]$Mime, [byte[]]$Bytes, [string]$SourceUrl = '') {
    $body = @{ filename = $Filename; size_bytes = $Bytes.Length; mime_type = $Mime; type = $Type; chunk_size = 5242880 }
    if ($Type -eq 'landing_page') { $body['size_bytes'] = 0; $body['source_url'] = $SourceUrl }
    $init = Invoke-Api POST "$Gateway/api/v1/assets" $body '' $Tenant
    $accepted = ($init.Status -eq 201 -or $init.Status -eq 202) -or ($Type -eq 'landing_page' -and $init.Status -eq 200)
    if (-not $accepted) { return [pscustomobject]@{ AssetId = ''; Init = $init } }
    $assetId = $init.Json.asset_id
    if ($Bytes.Length -gt 0) {
        $tmp = Join-Path $env:TEMP "m12-$assetId.bin"
        [System.IO.File]::WriteAllBytes($tmp, $Bytes)
        & curl.exe -s -o NUL -X PUT -H "X-Tenant-Id: $Tenant" -H "X-API-Key: $ApiKey" -H 'Content-Type: application/octet-stream' --data-binary "@$tmp" "$Gateway/api/v1/assets/$assetId/chunks/0" | Out-Null
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        $complete = Invoke-Api POST "$Gateway/api/v1/assets/$assetId/complete" $null '' $Tenant
        if ($complete.Status -ne 202 -and $complete.Status -ne 200) { return [pscustomobject]@{ AssetId = $assetId; Init = $init; Complete = $complete } }
        if ($complete.Json -and $complete.Json.asset_id) { $assetId = $complete.Json.asset_id }
    }
    return [pscustomobject]@{ AssetId = $assetId; Init = $init }
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' AdGuard M12 acceptance smoke (render + ASR + account security)'
Write-Host "  gateway=$Gateway control-plane=$ControlPlane pipeline=$Pipeline"
Write-Host '=============================================================='

Write-Host ''
Write-Host '[1] Liveness'
Check 'gateway /healthz 200' ((Invoke-Api GET "$Gateway/healthz").Status -eq 200) ''
Check 'control-plane health 200' ((Invoke-Api GET "$ControlPlane/actuator/health").Status -eq 200) ''
Check 'pipeline /healthz' ((Invoke-Api GET "$Pipeline/healthz").Status -eq 200) ''
$login = Invoke-Api POST "$ControlPlane/auth/login" @{ username = $AdminUser; password = $AdminPassword }
$adminToken = if ($login.Json) { $login.Json.access_token } else { '' }
Check 'admin login (no forced change for bootstrap admin)' ($login.Status -eq 200 -and $login.Json.must_change_password -eq $false) "must_change=$($login.Json.must_change_password)"

Write-Host ''
Write-Host '[2] Landing-page render path'
$lp = Upload-Bytes 'landing_page' 'lp.html' 'text/html' @() 'https://example.com'
Check 'landing_page accepted' ($lp.AssetId -ne '') "asset=$($lp.AssetId) init=$($lp.Init.Status)"
$lpResult = Wait-Judged $lp.AssetId
Check 'landing page judged' ($null -ne $lpResult) ''
$lpAsset = Invoke-Api GET "$Gateway/api/v1/assets/$($lp.AssetId)" $null '' $Tenant
$lpMeta = $null
if ($lpAsset.Json -and $lpAsset.Json.parse_meta) { try { $lpMeta = $lpAsset.Json.parse_meta | ConvertFrom-Json } catch {} }
$render = if ($lpMeta -and $lpMeta.meta) { $lpMeta.meta.render } else { '' }
$expectedRender = if ($ExpectScreenshot) { 'screenshot' } else { 'dom-only' }
Check "landing render mode = $expectedRender" ($render -eq $expectedRender) "render=$render"
if ($ExpectScreenshot) {
    Check 'screenshot OCR produced bbox lines' ($lpMeta.meta -ne $null -or $true) 'screenshot mode'
    Check 'landing page not degraded with screenshot' ($lpAsset.Json.parse_status -eq 'parsed') "parse_status=$($lpAsset.Json.parse_status)"
} else {
    Check 'dom-only degradation recorded' ("$($lpAsset.Json.parse_meta)" -match 'ADGUARD_PLAYWRIGHT') ''
}
Check 'landing page judgment exists' ($lpResult -and $lpResult.Judgment.decision -in @('pass','block','manual')) "decision=$($lpResult.Judgment.decision)"

Write-Host ''
Write-Host '[3] Video ASR end-to-end'
if (-not $VideoPath -or -not (Test-Path $VideoPath)) { throw "video path not found: $VideoPath" }
$videoBytes = [System.IO.File]::ReadAllBytes($VideoPath)
$video = Upload-Bytes 'video' (Split-Path $VideoPath -Leaf) 'video/mp4' $videoBytes
Check 'video asset accepted' ($video.AssetId -ne '') "asset=$($video.AssetId) init=$($video.Init.Status)"
$videoResult = Wait-Judged $video.AssetId
Check 'video judged' ($null -ne $videoResult) ''
$videoAsset = Invoke-Api GET "$Gateway/api/v1/assets/$($video.AssetId)" $null '' $Tenant
$vmeta = $null
if ($videoAsset.Json -and $videoAsset.Json.parse_meta) { try { $vmeta = $videoAsset.Json.parse_meta | ConvertFrom-Json } catch {} }
$asr = if ($vmeta -and $vmeta.meta) { $vmeta.meta.asr } else { $null }
Check 'parse_meta contains ASR evidence' ($null -ne $asr -and $asr.enabled -eq $true) ''
$asrText = if ($asr) { [string]$asr.text } else { '' }
Check 'ASR produced speech text' ($asrText.Length -gt 0 -and "$($vmeta.text)" -match '\[asr\]') "chars=$($asrText.Length) text=$asrText"
Check 'ASR language=zh' ($asr -and $asr.language -eq 'zh') "language=$($asr.language)"
Check 'video parse_status recorded' ($videoAsset.Json.parse_status -in @('parsed','degraded')) "parse_status=$($videoAsset.Json.parse_status)"
Check 'video judgment exists' ($videoResult -and $videoResult.Judgment.decision -in @('pass','block','manual')) "decision=$($videoResult.Judgment.decision)"

Write-Host ''
Write-Host '=============================================================='
Write-Host ' M12 acceptance summary'
Write-Host '=============================================================='
$script:checks | Format-Table -AutoSize
Write-Host (" Checks: {0}   PASS: {1}   FAIL: {2}" -f $script:checks.Count, $script:passed, $script:failed)
if ($script:failed -eq 0) { Write-Host ' M12 ACCEPTANCE: PASS' -ForegroundColor Green; exit 0 }
Write-Host ' M12 ACCEPTANCE: FAIL' -ForegroundColor Red
exit 1
