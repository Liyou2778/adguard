# AdGuard M7 acceptance smoke test: multimodal parsing (image OCR/bbox), landing-page URL,
# antivirus quarantine. Runs against a local or cloud gateway.
param(
    [string]$Gateway = 'http://127.0.0.1:8080',
    [string]$ControlPlane = 'http://127.0.0.1:8081',
    [string]$Tenant = 'demo-platform',
    [int]$PollSeconds = 60
)
$ErrorActionPreference = 'Stop'
$script:passed = 0
$script:failed = 0
$script:checks = @()

function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { $script:passed++; $status = 'PASS' } else { $script:failed++; $status = 'FAIL' }
    $script:checks += [pscustomobject]@{ Check = $Name; Status = $status; Detail = $Detail }
    $color = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}  {2}" -f $status, $Name, $Detail) -ForegroundColor $color
}

function Invoke-Json([string]$Method, [string]$Url, $Body = $null, [string]$TenantId = '') {
    $headers = @{}
    if ($TenantId) { $headers['X-Tenant-Id'] = $TenantId }
    $json = $null
    if ($null -ne $Body) { $json = $Body | ConvertTo-Json -Depth 8 -Compress }
    $body = ''
    $status = 0
    try {
        if ($null -ne $json) {
            $response = Invoke-WebRequest -Uri $Url -Method $Method -Headers $headers -UseBasicParsing `
                -ContentType 'application/json; charset=utf-8' -Body $json -TimeoutSec 60
        } else {
            $response = Invoke-WebRequest -Uri $Url -Method $Method -Headers $headers -UseBasicParsing -TimeoutSec 60
        }
        $status = [int]$response.StatusCode
        $body = $response.Content
    } catch {
        $response = $_.Exception.Response
        if ($null -ne $response) {
            $status = [int]$response.StatusCode
            try {
                $stream = $response.GetResponseStream()
                if ($stream) {
                    $reader = New-Object System.IO.StreamReader($stream)
                    $body = $reader.ReadToEnd()
                    $reader.Close()
                }
            } catch {}
        }
    }
    return [pscustomobject]@{ Status = $status; Body = $body }
}

function Invoke-JsonParsed([string]$Method, [string]$Url, $Body = $null, [string]$TenantId = '') {
    $result = Invoke-Json -Method $Method -Url $Url -Body $Body -TenantId $TenantId
    $parsed = $null
    if ($result.Body) { try { $parsed = $result.Body | ConvertFrom-Json } catch {} }
    return [pscustomobject]@{ Status = $result.Status; Body = $result.Body; Json = $parsed }
}

function Upload-Asset([string]$Type, [string]$Filename, [string]$Mime, [byte[]]$Bytes, [string]$SourceUrl = '') {
    $initBody = @{ filename = $Filename; size_bytes = $Bytes.Length; mime_type = $Mime; type = $Type; chunk_size = 5242880 }
    if ($SourceUrl) { $initBody['source_url'] = $SourceUrl; $initBody['size_bytes'] = 0 }
    $init = Invoke-JsonParsed POST "$Gateway/api/v1/assets" $initBody $Tenant
    if ($init.Status -ne 201 -and $init.Status -ne 202) { return $init }
    $assetId = if ($init.Json) { $init.Json.asset_id } else { '' }
    if ($init.Status -eq 201 -and $Bytes.Length -gt 0) {
        $tmp = Join-Path $env:TEMP ("adguard-m7-{0}.bin" -f $assetId)
        [System.IO.File]::WriteAllBytes($tmp, $Bytes)
        try {
            & curl.exe -s -o NUL -X PUT -H "X-Tenant-Id: $Tenant" -H 'Content-Type: application/octet-stream' `
                --data-binary "@$tmp" "$Gateway/api/v1/assets/$assetId/chunks/0" | Out-Null
        } finally {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        }
        $complete = Invoke-JsonParsed POST "$Gateway/api/v1/assets/$assetId/complete" $null $Tenant
        return [pscustomobject]@{ Status = $complete.Status; Json = [pscustomobject]@{ asset_id = $assetId } }
    }
    return $init
}

function Wait-Judgment([string]$AssetId) {
    for ($i = 0; $i -lt $PollSeconds; $i++) {
        Start-Sleep -Seconds 1
        $asset = Invoke-JsonParsed GET "$Gateway/api/v1/assets/$AssetId" $null $Tenant
        if (-not $asset.Json) { continue }
        if ($asset.Json.latest_judgment_id) {
            $judgment = Invoke-JsonParsed GET "$Gateway/api/v1/judgments/$($asset.Json.latest_judgment_id)" $null $Tenant
            return [pscustomobject]@{ Asset = $asset.Json; Judgment = $judgment.Json; JudgmentHttp = $judgment.Status }
        }
        if ($asset.Json.status -eq 'failed') { return [pscustomobject]@{ Asset = $asset.Json; Judgment = $null } }
    }
    return $null
}

function New-PromoImage() {
    Add-Type -AssemblyName System.Drawing
    $bitmap = New-Object System.Drawing.Bitmap 900, 420
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.Clear([System.Drawing.Color]::White)
    $font = New-Object System.Drawing.Font('Microsoft YaHei', 46, [System.Drawing.FontStyle]::Bold)
    $small = New-Object System.Drawing.Font('Microsoft YaHei', 30)
    $brush = [System.Drawing.Brushes]::Black
    $graphics.DrawString('全网最低价', $font, $brush, 40, 40)
    $graphics.DrawString('限时抢购 买到就是赚到', $small, $brush, 60, 220)
    $graphics.DrawString('广告', (New-Object System.Drawing.Font('Microsoft YaHei', 18)), $brush, 780, 360)
    $stamp = Get-Date -Format 'HHmmssfff'
    $graphics.DrawString('批次 ' + $stamp, (New-Object System.Drawing.Font('Microsoft YaHei', 16)), $brush, 40, 370)
    $graphics.Dispose()
    $stream = New-Object System.IO.MemoryStream
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $stream.ToArray()
    $stream.Dispose(); $bitmap.Dispose()
    return $bytes
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' AdGuard M7 acceptance smoke test (multimodal parsing)'
Write-Host "  gateway=$Gateway  control-plane=$ControlPlane  tenant=$Tenant"
Write-Host '=============================================================='

Write-Host ''
Write-Host '[1] Service liveness'
Check 'gateway /healthz 200' ((Invoke-JsonParsed GET "$Gateway/healthz").Status -eq 200) ''
Check 'control-plane health 200' ((Invoke-JsonParsed GET "$ControlPlane/actuator/health").Status -eq 200) ''

Write-Host ''
Write-Host '[2] Rule + snapshot (absolute-terms family matches 最低价)'
$ruleId = 'ADV-M7-' + (Get-Date -Format 'yyyyMMddHHmmss')
$rule = @{
    rule_id = $ruleId; law = '中华人民共和国广告法'; article = '第九条'; item = '（三）'
    family = 'absolute_terms'; severity = 'high'
    text = '不得使用“国家级”“最高级”“最佳”等用语'
    canonical_terms = @('最好', '最佳', '最高级', '国家级')
    detection = @{ lexical = @('regex:最[好佳优低高强]'); semantic = '判断是否绝对化宣称' }
    penalty = @{ law_ref = '第五十七条'; min = 200000; max = 1000000 }
    effective_from = '2015-09-01'; source_url = 'https://example.gov.cn/ad-law'
}
$created = Invoke-JsonParsed POST "$ControlPlane/admin/rules" $rule $Tenant
Check 'POST /admin/rules 201' ($created.Status -eq 201) "status=$($created.Status)"
$activated = Invoke-JsonParsed POST "$ControlPlane/admin/rules/$ruleId/activate" $null $Tenant
Check 'activate snapshot 200' ($activated.Status -eq 200) "snapshot=$($activated.Json.id)"

Write-Host ''
Write-Host '[3] Image asset -> OCR -> rule judgment with bbox'
$image = New-PromoImage
Write-Host ("  generated creative: {0} bytes" -f $image.Length)
$upload = Upload-Asset 'image' 'creative.png' 'image/png' $image
Check 'image upload accepted' ($upload.Status -eq 202) "status=$($upload.Status) asset=$($upload.Json.asset_id)"
$result = Wait-Judgment $upload.Json.asset_id
Check 'image reached judged' ($null -ne $result -and $null -ne $result.Judgment) ''
if ($result -and $result.Judgment) {
    Check 'OCR image decision=block' ($result.Judgment.decision -eq 'block') "decision=$($result.Judgment.decision)"
    $violation = $null
    if ($result.Judgment.violations -and $result.Judgment.violations.Count -gt 0) { $violation = $result.Judgment.violations[0] }
    Check 'violation present' ($null -ne $violation) ''
    $bbox = if ($violation) { $violation.bbox } else { $null }
    Check 'violation carries OCR bbox' ($bbox -and $bbox.Count -eq 4) "bbox=$($bbox -join ',')"
    Check 'asset parse_status=parsed' ($result.Asset.parse_status -eq 'parsed') "parse_status=$($result.Asset.parse_status)"
    $parseMeta = "$($result.Asset.parse_meta)"
    Check 'parse_meta records OCR engine' ($parseMeta -match 'rapidocr') ''
    Check 'parse_meta records role classification' ($parseMeta -match 'role_counts') ''
}

Write-Host ''
Write-Host '[4] Antivirus quarantine (EICAR text asset)'
$eicar = 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
$virusBytes = [System.Text.Encoding]::UTF8.GetBytes($eicar + "`nrun " + (Get-Date -Format 'HHmmssfff'))
$virusUpload = Upload-Asset 'text' 'eicar.txt' 'text/plain' $virusBytes
$virusResult = Wait-Judgment $virusUpload.Json.asset_id
Check 'EICAR asset reached judged' ($null -ne $virusResult -and $null -ne $virusResult.Judgment) ''
if ($virusResult -and $virusResult.Judgment) {
    Check 'EICAR decision=manual' ($virusResult.Judgment.decision -eq 'manual') "decision=$($virusResult.Judgment.decision)"
    Check 'EICAR reason mentions antivirus' ("$($virusResult.Judgment.reason)" -match 'antivirus') "reason=$($virusResult.Judgment.reason)"
}

Write-Host ''
Write-Host '[5] Landing page asset (URL ingestion, DOM text)'
$url = 'https://example.com'
$lp = Invoke-JsonParsed POST "$Gateway/api/v1/assets" @{ filename = 'page'; size_bytes = 0; mime_type = 'text/html'; type = 'landing_page'; source_url = $url } $Tenant
Check 'landing_page accepted' ($lp.Status -eq 202 -or $lp.Status -eq 200) "status=$($lp.Status) asset=$($lp.Json.asset_id)"
$lpResult = Wait-Judgment $lp.Json.asset_id
Check 'landing_page reached judged' ($null -ne $lpResult -and $null -ne $lpResult.Judgment) ''
if ($lpResult -and $lpResult.Judgment) {
    Check 'landing_page decision in (pass,block,manual)' ($lpResult.Judgment.decision -in @('pass','block','manual')) "decision=$($lpResult.Judgment.decision)"
    Check 'landing_page parse_status recorded' ($lpResult.Asset.parse_status -in @('parsed','degraded')) "parse_status=$($lpResult.Asset.parse_status)"
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' M7 acceptance summary'
Write-Host '=============================================================='
$script:checks | Format-Table -AutoSize
Write-Host ''
Write-Host (" Checks: {0}   PASS: {1}   FAIL: {2}" -f $script:checks.Count, $script:passed, $script:failed)
if ($script:failed -eq 0) {
    Write-Host ' M7 ACCEPTANCE: PASS' -ForegroundColor Green
    exit 0
}
Write-Host ' M7 ACCEPTANCE: FAIL' -ForegroundColor Red
exit 1
