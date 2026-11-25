# M10 (FR-6) acceptance smoke: caption ingestion, cross-modal consistency, manual routing.
param(
    [string]$Gateway = 'http://127.0.0.1:8080',
    [string]$ControlPlane = 'http://127.0.0.1:8081',
    [string]$Pipeline = 'http://127.0.0.1:8090',
    [string]$AdminUser = 'admin',
    [string]$AdminPassword = '',
    [string]$AdminKey = '',
    [string]$ApiKey = '',
    [string]$Tenant = 'demo-platform',
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
function Invoke-Api([string]$Method, [string]$Url, $Body = $null, [string]$Token = '', [string]$TenantHeader = '', [switch]$Raw) {
    $headers = @{}
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    if ($TenantHeader) { $headers['X-Tenant-Id'] = $TenantHeader }
    if ($AdminKey) { $headers['X-Admin-Key'] = $AdminKey }
    if ($ApiKey) { $headers['X-API-Key'] = $ApiKey }
    $resp = $null; $content = ''
    try {
        $params = @{ Uri = $Url; Method = $Method; Headers = $headers; UseBasicParsing = $true; TimeoutSec = 90 }
        if ($null -ne $Body) {
            $json = $Body | ConvertTo-Json -Depth 12 -Compress
            $params['ContentType'] = 'application/json; charset=utf-8'
            $params['Body'] = [System.Text.Encoding]::UTF8.GetBytes($json)
        }
        $resp = Invoke-WebRequest @params
        $content = $resp.Content
    } catch {
        $resp = $_.Exception.Response
        if ($null -ne $_.ErrorDetails -and $_.ErrorDetails.Message) {
            $content = [string]$_.ErrorDetails.Message
        } elseif ($null -ne $resp) {
            try { $sr = New-Object System.IO.StreamReader($resp.GetResponseStream()); $content = $sr.ReadToEnd(); $sr.Close() } catch {}
        }
    }
    $status = 0; if ($null -ne $resp) { $status = [int]$resp.StatusCode }
    if ($Raw) { return [pscustomobject]@{ Status = $status; Body = $content; Raw = $resp } }
    $json = $null
    if ($content) { try { $json = $content | ConvertFrom-Json } catch {} }
    return [pscustomobject]@{ Status = $status; Body = $content; Json = $json }
}
function New-PromoImage([string]$Text) {
    Add-Type -AssemblyName System.Drawing
    $bitmap = New-Object System.Drawing.Bitmap 900, 360
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.Clear([System.Drawing.Color]::White)
    $font = New-Object System.Drawing.Font('Microsoft YaHei', 44, [System.Drawing.FontStyle]::Bold)
    $brush = [System.Drawing.Brushes]::Black
    $graphics.DrawString($Text, $font, $brush, 50, 80)
    $graphics.DrawString('限时抢购', (New-Object System.Drawing.Font('Microsoft YaHei', 26)), $brush, 80, 220)
    $graphics.DrawString('批次 ' + (Get-Date -Format 'HHmmssfff'), (New-Object System.Drawing.Font('Microsoft YaHei', 16)), $brush, 50, 305)
    $graphics.Dispose()
    $stream = New-Object System.IO.MemoryStream
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $stream.ToArray()
    $stream.Dispose(); $bitmap.Dispose()
    return $bytes
}
function Upload-Image([string]$Caption, [string]$ImageText, [string]$Tag) {
    $bytes = New-PromoImage $ImageText
    $size = $bytes.Length
    $body = @{ filename = "m10-$Tag.png"; size_bytes = $size; mime_type = 'image/png'; type = 'image'; chunk_size = 5242880 }
    if ($Caption) { $body['caption'] = $Caption }
    $init = Invoke-Api POST "$Gateway/api/v1/assets" $body '' $Tenant
    if ($init.Status -ne 201) { return [pscustomobject]@{ Init = $init } }
    $assetId = $init.Json.asset_id
    $tmp = Join-Path $env:TEMP "m10-$assetId.bin"
    [System.IO.File]::WriteAllBytes($tmp, $bytes)
    & curl.exe -s -o NUL -X PUT -H "X-Tenant-Id: $Tenant" -H "X-API-Key: $ApiKey" -H 'Content-Type: application/octet-stream' --data-binary "@$tmp" "$Gateway/api/v1/assets/$assetId/chunks/0" | Out-Null
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    $complete = Invoke-Api POST "$Gateway/api/v1/assets/$assetId/complete" $null '' $Tenant
    if ($complete.Json -and $complete.Json.asset_id) { $assetId = $complete.Json.asset_id }
    for ($i = 0; $i -lt $PollSeconds; $i++) {
        Start-Sleep -Seconds 1
        $asset = Invoke-Api GET "$Gateway/api/v1/assets/$assetId" $null '' $Tenant
        if ($asset.Json -and $asset.Json.latest_judgment_id) {
            $judgment = Invoke-Api GET "$Gateway/api/v1/judgments/$($asset.Json.latest_judgment_id)" $null '' $Tenant
            return [pscustomobject]@{ Init = $init; Complete = $complete; Asset = $asset.Json; Judgment = $judgment.Json }
        }
        if ($asset.Json -and $asset.Json.status -eq 'failed') { return [pscustomobject]@{ Init = $init; Complete = $complete; Asset = $asset.Json } }
    }
    return [pscustomobject]@{ Init = $init; Complete = $complete; Asset = $null; Judgment = $null }
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' AdGuard M10 acceptance smoke (FR-6 image/caption consistency)'
Write-Host "  gateway=$Gateway control-plane=$ControlPlane pipeline=$Pipeline"
Write-Host '=============================================================='

Write-Host ''
Write-Host '[1] Liveness + consistency endpoint'
Check 'gateway /healthz 200' ((Invoke-Api GET "$Gateway/healthz").Status -eq 200) ''
Check 'pipeline /healthz 200' ((Invoke-Api GET "$Pipeline/healthz").Status -eq 200) ''
$directBody = @{
    caption = '到手价 99 元'
    image_text = '活动价 199 元'
    lines = @(@{ text = '活动价 199 元'; bbox = @(10, 20, 200, 60) })
}
$direct = Invoke-Api POST "$Pipeline/v1/consistency" $directBody
$directConflict = $direct.Json.conflicts[0]
Check 'rule conflict detected via /v1/consistency' ($direct.Status -eq 200 -and $direct.Json.conflicts.Count -ge 1) "status=$($direct.Status)"
Check 'conflict carries attribute + evidence + bbox' ($directConflict -and $directConflict.attribute -eq 'price' -and $directConflict.bbox.Count -eq 4) ''
$semantic = Invoke-Api POST "$Pipeline/v1/consistency" @{ caption = '全国包邮'; image_text = '偏远地区不包邮' }
$llmOk = ($semantic.Status -eq 200 -and $semantic.Json.conflicts.Count -ge 1 -and $semantic.Json.llm_used -eq $true)
Check 'LLM gray-zone semantic conflict detected' $llmOk "llm_used=$($semantic.Json.llm_used) engine=$($semantic.Json.engine)"

Write-Host ''
Write-Host '[2] Admin + rule snapshot'
$login = Invoke-Api POST "$ControlPlane/auth/login" @{ username = $AdminUser; password = $AdminPassword }
$adminToken = if ($login.Json) { $login.Json.access_token } else { '' }
Check 'admin login 200 + JWT' ($login.Status -eq 200 -and $adminToken -ne '') "status=$($login.Status)"
$ruleId = 'ADV-M10-' + (Get-Date -Format 'HHmmss')
$rule = @{
    rule_id = $ruleId; law = '中华人民共和国广告法'; article = '第九条'; item = '（三）'
    family = 'absolute_terms'; severity = 'high'; text = '不得使用“国家级”“最高级”“最佳”等用语'
    canonical_terms = @('最好', '最佳'); detection = @{ lexical = @('regex:最[好佳]'); semantic = '一致性测试' }
    penalty = @{ law_ref = '第五十七条'; min = 200000; max = 1000000 }
    effective_from = '2015-09-01'; source_url = 'https://example.gov.cn/ad-law'
}
$created = Invoke-Api POST "$ControlPlane/admin/rules" $rule '' $Tenant
$activated = Invoke-Api POST "$ControlPlane/admin/rules/$ruleId/activate" $null '' $Tenant
Check 'rule created + snapshot activated' ($created.Status -eq 201 -and $activated.Status -eq 200) "rule=$($created.Status) act=$($activated.Status)"

Write-Host ''
Write-Host '[3] Caption conflict -> manual review with consistency violation'
$conflictRun = Upload-Image '到手价 99 元，限时抢购' '活动价 199 元' ('c' + (Get-Date -Format 'HHmmss'))
Check 'asset created with caption' ($conflictRun.Init.Status -eq 201 -and $conflictRun.Init.Json -eq $null -eq $false) "status=$($conflictRun.Init.Status)"
Check 'judged asset recorded' ($null -ne $conflictRun.Judgment) ''
$consistencyViolation = $null
if ($conflictRun.Judgment -and $conflictRun.Judgment.violations) {
    $consistencyViolation = $conflictRun.Judgment.violations | Where-Object { $_.family -eq 'cross_modal_consistency' } | Select-Object -First 1
}
Check 'decision routed to manual review' ($conflictRun.Judgment.decision -eq 'manual') "decision=$($conflictRun.Judgment.decision)"
Check 'violation family=cross_modal_consistency' ($null -ne $consistencyViolation) ''
Check 'violation rule_id=CONSISTENCY-PRICE' ($consistencyViolation -and $consistencyViolation.rule_id -eq 'CONSISTENCY-PRICE') "rule=$($consistencyViolation.rule_id)"
Check 'violation carries both evidence snippets' ($consistencyViolation -and $consistencyViolation.evidence_text -match '99' -and $consistencyViolation.evidence_text -match '199') "evidence=$($consistencyViolation.evidence_text)"
$assetDetail = Invoke-Api GET "$Gateway/api/v1/assets/$($conflictRun.Asset.asset_id)" $null '' $Tenant
$parseMeta = "$($assetDetail.Json.parse_meta)"
Check 'asset.caption persisted' ($assetDetail.Json.caption -eq '到手价 99 元，限时抢购') "caption=$($assetDetail.Json.caption)"
Check 'parse_meta records consistency evidence' ($parseMeta -match 'consistency' -and $parseMeta -match 'conflicts') ''

Write-Host ''
Write-Host '[4] Consistent caption -> no consistency violation; captionless asset skips check'
$okRun = Upload-Image '活动价 199 元 限时抢购' '活动价 199 元' ('n' + (Get-Date -Format 'HHmmss'))
$okViolation = $null
if ($okRun.Judgment -and $okRun.Judgment.violations) {
    $okViolation = $okRun.Judgment.violations | Where-Object { $_.family -eq 'cross_modal_consistency' } | Select-Object -First 1
}
Check 'consistent caption produces no cross-modal violation' ($null -eq $okViolation) "decision=$($okRun.Judgment.decision)"
$assetOk = Invoke-Api GET "$Gateway/api/v1/assets/$($okRun.Asset.asset_id)" $null '' $Tenant
Check 'consistent asset parsed without inconsistency' ("$($assetOk.Json.parse_meta)" -notmatch 'cross_modal') ''
$noCaptionRun = Upload-Image '' '活动价 199 元' ('x' + (Get-Date -Format 'HHmmss'))
$assetNoCaption = Invoke-Api GET "$Gateway/api/v1/assets/$($noCaptionRun.Asset.asset_id)" $null '' $Tenant
Check 'captionless asset skips consistency check' ("$($assetNoCaption.Json.parse_meta)" -notmatch '"consistency"') ''

Write-Host ''
Write-Host '=============================================================='
Write-Host ' M10 acceptance summary'
Write-Host '=============================================================='
$script:checks | Format-Table -AutoSize
Write-Host (" Checks: {0}   PASS: {1}   FAIL: {2}" -f $script:checks.Count, $script:passed, $script:failed)
if ($script:failed -eq 0) { Write-Host ' M10 ACCEPTANCE: PASS' -ForegroundColor Green; exit 0 }
Write-Host ' M10 ACCEPTANCE: FAIL' -ForegroundColor Red
exit 1
