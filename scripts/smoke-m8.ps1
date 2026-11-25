# M8 (FR-10) acceptance smoke: identity/roles, merchant appeal -> reviewer decision -> feedback pair.
param(
    [string]$AdminKey = '',
    [string]$Gateway = 'http://127.0.0.1:8080',
    [string]$ControlPlane = 'http://127.0.0.1:8081',
    [string]$AdminUser = 'admin',
    [string]$AdminPassword = '',
    [string]$Tenant = 'demo-platform',
    [int]$PollSeconds = 45,
    [switch]$AuthRequired
)
$ErrorActionPreference = 'Stop'
$script:passed = 0; $script:failed = 0; $script:checks = @()
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { $script:passed++; $status = 'PASS' } else { $script:failed++; $status = 'FAIL' }
    $script:checks += [pscustomobject]@{ Check = $Name; Status = $status; Detail = $Detail }
    $color = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}  {2}" -f $status, $Name, $Detail) -ForegroundColor $color
}
function Invoke-Api([string]$Method, [string]$Url, $Body = $null, [string]$Token = '', [string]$TenantHeader = '', [switch]$Raw, [switch]$Anonymous) {
    $headers = @{}
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    if ($AdminKey -and -not $Anonymous) { $headers['X-Admin-Key'] = $AdminKey }
    if ($TenantHeader) { $headers['X-Tenant-Id'] = $TenantHeader }
    $resp = $null; $content = ''
    try {
        $params = @{ Uri = $Url; Method = $Method; Headers = $headers; UseBasicParsing = $true; TimeoutSec = 60 }
        if ($null -ne $Body) { $params['ContentType'] = 'application/json; charset=utf-8'; $params['Body'] = ($Body | ConvertTo-Json -Depth 10 -Compress) }
        $resp = Invoke-WebRequest @params
        $content = $resp.Content
    } catch {
        $resp = $_.Exception.Response
        if ($null -ne $resp) {
            try { $sr = New-Object System.IO.StreamReader($resp.GetResponseStream()); $content = $sr.ReadToEnd(); $sr.Close() } catch {}
        }
    }
    $status = 0; if ($null -ne $resp) { $status = [int]$resp.StatusCode }
    if ($Raw) { return [pscustomobject]@{ Status = $status; Body = $content; Raw = $resp } }
    $json = $null
    if ($content) { try { $json = $content | ConvertFrom-Json } catch {} }
    return [pscustomobject]@{ Status = $status; Body = $content; Json = $json }
}
function Wait-Judged([string]$AssetId) {
    for ($i = 0; $i -lt $PollSeconds; $i++) {
        Start-Sleep -Seconds 1
        $asset = Invoke-Api GET "$Gateway/api/v1/assets/$AssetId" $null '' $Tenant
        if ($asset.Json -and $asset.Json.latest_judgment_id) {
            return Invoke-Api GET "$Gateway/api/v1/judgments/$($asset.Json.latest_judgment_id)" $null '' $Tenant
        }
        if ($asset.Json -and $asset.Json.status -eq 'failed') { return $null }
    }
    return $null
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' AdGuard M8 acceptance smoke (identity + appeal feedback loop)'
Write-Host "  gateway=$Gateway control-plane=$ControlPlane tenant=$Tenant"
Write-Host '=============================================================='

Write-Host ''
Write-Host '[1] Service liveness'
Check 'gateway /healthz 200' ((Invoke-Api GET "$Gateway/healthz").Status -eq 200) ''
Check 'control-plane health 200' ((Invoke-Api GET "$ControlPlane/actuator/health").Status -eq 200) ''
Check 'admin UI served' ((Invoke-Api GET "$ControlPlane/admin-ui/index.html").Status -eq 200) ''

Write-Host ''
Write-Host '[2] Authentication'
$bad = Invoke-Api POST "$ControlPlane/auth/login" @{ username = $AdminUser; password = 'wrong-password-123' }
Check 'wrong password -> 401' ($bad.Status -eq 401) "status=$($bad.Status)"
$login = Invoke-Api POST "$ControlPlane/auth/login" @{ username = $AdminUser; password = $AdminPassword }
$adminToken = if ($login.Json) { $login.Json.access_token } else { '' }
Check 'admin login 200 + JWT' ($login.Status -eq 200 -and $adminToken.Length -gt 40) "status=$($login.Status)"
$me = Invoke-Api GET "$ControlPlane/auth/me" $null $adminToken
Check 'GET /auth/me role=admin' ($me.Json -and $me.Json.role -eq 'admin') "role=$($me.Json.role)"
$unauth = Invoke-Api GET "$ControlPlane/admin/users" $null '' '' -Anonymous
if ($AuthRequired) {
    Check 'unauthenticated /admin/users -> 401 (prod mode)' ($unauth.Status -eq 401) "status=$($unauth.Status)"
} else {
    Check 'unauthenticated /admin/users allowed only in dev mode' ($unauth.Status -eq 200) "dev mode status=$($unauth.Status)"
}

Write-Host ''
Write-Host '[3] Tenant user provisioning (admin -> merchant)'
$username = 'smoke-' + (Get-Date -Format 'HHmmss')
$create = Invoke-Api POST "$ControlPlane/admin/users" @{ username = $username; password = 'SmokePass123!'; role = 'owner'; tenant_id = $Tenant; email = "$username@example.com" } $adminToken
Check 'admin creates tenant owner 201' ($create.Status -eq 201) "status=$($create.Status)"
$merchantLogin = Invoke-Api POST "$ControlPlane/auth/login" @{ username = $username; password = 'SmokePass123!' }
$merchantToken = if ($merchantLogin.Json) { $merchantLogin.Json.access_token } else { '' }
Check 'merchant login 200 + tenant claim' ($merchantLogin.Status -eq 200 -and $merchantLogin.Json.user.tenant_id -eq $Tenant) "tenant=$($merchantLogin.Json.user.tenant_id)"
$forbidden = Invoke-Api GET "$ControlPlane/admin/users" $null $merchantToken
Check 'merchant -> /admin/users 403' ($forbidden.Status -eq 403) "status=$($forbidden.Status)"

Write-Host ''
Write-Host '[4] Produce a real judgment (rule + text asset)'
$ruleId = 'ADV-M8-' + (Get-Date -Format 'HHmmss')
$rule = @{
    rule_id = $ruleId; law = '中华人民共和国广告法'; article = '第九条'; item = '（三）'
    family = 'absolute_terms'; severity = 'high'; text = '不得使用“国家级”“最高级”“最佳”等用语'
    canonical_terms = @('最好', '最佳', '最高级', '国家级')
    detection = @{ lexical = @('regex:最[好佳优低高强]'); semantic = '判断是否绝对化宣称' }
    penalty = @{ law_ref = '第五十七条'; min = 200000; max = 1000000 }
    effective_from = '2015-09-01'; source_url = 'https://example.gov.cn/ad-law'
}
$created = Invoke-Api POST "$ControlPlane/admin/rules" $rule '' $Tenant
$activated = Invoke-Api POST "$ControlPlane/admin/rules/$ruleId/activate" $null '' $Tenant
Check 'rule created + snapshot activated' ($created.Status -eq 201 -and $activated.Status -eq 200) "rule=$($created.Status) act=$($activated.Status)"
$body = @{ filename = 'appeal.txt'; size_bytes = 0; mime_type = 'text/plain'; type = 'text'; chunk_size = 1048576 } 
$textBytes = [System.Text.Encoding]::UTF8.GetBytes('全网最低价，错过再等一年 ' + (Get-Date -Format 'HHmmssfff'))
$body['size_bytes'] = $textBytes.Length
$init = Invoke-Api POST "$Gateway/api/v1/assets" $body '' $Tenant
$assetId = if ($init.Json) { $init.Json.asset_id } else { '' }
Check 'asset created' ($init.Status -eq 201) "asset=$assetId"
$tmp = Join-Path $env:TEMP "m8-$assetId.bin"
[System.IO.File]::WriteAllBytes($tmp, $textBytes)
& curl.exe -s -o NUL -X PUT -H "X-Tenant-Id: $Tenant" -H 'Content-Type: application/octet-stream' --data-binary "@$tmp" "$Gateway/api/v1/assets/$assetId/chunks/0" | Out-Null
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
$complete = Invoke-Api POST "$Gateway/api/v1/assets/$assetId/complete" $null '' $Tenant
Check 'asset upload completed (202)' ($complete.Status -eq 202) "status=$($complete.Status)"
$judgment = Wait-Judged $assetId
$judgmentId = if ($judgment.Json) { $judgment.Json.judgment_id } else { '' }
$decision = if ($judgment.Json) { $judgment.Json.decision } else { '' }
Check 'judgment produced and appealable (block/manual)' ($judgmentId -ne '' -and $decision -in @('block','manual')) "decision=$decision"
$createdAt = if ($judgment.Json) { $judgment.Json.created_at } else { (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') }
$billedDate = ([datetime]$createdAt).ToString('yyyy-MM-dd')

Write-Host ''
Write-Host '[5] Merchant appeal'
$appealBody = @{ judgment_id = $judgmentId; reason = 'too_strict'; detail = '价格表述为活动文案，非绝对化承诺，申请复核。' }
$appeal = Invoke-Api POST "$ControlPlane/api/v1/appeals" $appealBody $merchantToken
$appealId = if ($appeal.Json) { $appeal.Json.id } else { '' }
Check 'create appeal 201' ($appeal.Status -eq 201) "status=$($appeal.Status) body=$($appeal.Body)"
$dup = Invoke-Api POST "$ControlPlane/api/v1/appeals" $appealBody $merchantToken
Check 'second open appeal -> 409' ($dup.Status -eq 409) "status=$($dup.Status)"
$mine = Invoke-Api GET "$ControlPlane/api/v1/appeals" $null $merchantToken
Check 'merchant lists own appeals' ($mine.Status -eq 200 -and $mine.Json.Count -ge 1) "count=$($mine.Json.Count)"

Write-Host ''
Write-Host '[6] Reviewer decision + DPO feedback pair'
$open = Invoke-Api GET "$ControlPlane/admin/appeals?status=open&limit=200" $null $adminToken
Check 'admin lists open appeals' ($open.Status -eq 200) "status=$($open.Status)"
$detail = Invoke-Api GET "$ControlPlane/admin/appeals/$appealId" $null $adminToken
Check 'appeal detail includes judgment + evidence' ($detail.Status -eq 200 -and $detail.Json.judgment -and $detail.Json.asset) "status=$($detail.Status)"
$review = Invoke-Api PATCH "$ControlPlane/admin/appeals/$appealId/review" @{ final_decision = 'pass'; comment = '活动价格有明确的活动期限，不构成绝对化用语，改判通过' } $adminToken
$pairId = if ($review.Json) { $review.Json.feedback_pair_id } else { '' }
Check 'review 200 + accepted + feedback_pair' ($review.Status -eq 200 -and $review.Json.review_result -eq 'accepted' -and $pairId -ne '') "result=$($review.Json.review_result) pair=$pairId"
$pairs = Invoke-Api GET "$ControlPlane/admin/feedback-pairs?used_in_dpo=false&limit=200" $null $adminToken
$containsPair = $false
if ($pairs.Json) { $containsPair = @($pairs.Json | Where-Object { $_.id -eq $pairId }).Count -gt 0 }
Check 'feedback pair listed as unused (used_in_dpo=false)' ($containsPair) ''
$mark = Invoke-Api POST "$ControlPlane/admin/feedback-pairs/$pairId/mark-used" $null $adminToken
Check 'mark pair used_in_dpo=1' ($mark.Status -eq 200) "status=$($mark.Status)"
$markAgain = Invoke-Api POST "$ControlPlane/admin/feedback-pairs/$pairId/mark-used" $null $adminToken
Check 'mark used again -> 404' ($markAgain.Status -eq 404) "status=$($markAgain.Status)"

Write-Host ''
Write-Host '[7] Usage + billing export'
$usage = Invoke-Api GET "$ControlPlane/api/v1/usage?from=$billedDate&to=$billedDate" $null $merchantToken
Check 'merchant usage query 200' ($usage.Status -eq 200) "status=$($usage.Status)"
$summary = Invoke-Api GET "$ControlPlane/admin/billing/summary" $null $adminToken
Check 'admin billing summary 200 + prices' ($summary.Status -eq 200 -and $summary.Json.prices.Count -ge 4) "status=$($summary.Status)"
$csv = Invoke-Api GET "$ControlPlane/admin/billing/export.csv" $null $adminToken -Raw
Check 'billing CSV 200 + BOM + header' ($csv.Status -eq 200 -and $csv.Body.StartsWith([char]0xFEFF) -and $csv.Body -match 'tenant_id') ''
$pdf = Invoke-Api GET "$ControlPlane/admin/billing/export.pdf" $null $adminToken -Raw
$pdfOk = $false
try {
    $resp = $pdf.Raw
    $stream = $resp.GetResponseStream()
    $ms = New-Object System.IO.MemoryStream
    $stream.CopyTo($ms)
    $bytes = $ms.ToArray()
    $pdfOk = ($bytes.Length -gt 1000 -and $bytes[0] -eq 0x25 -and $bytes[1] -eq 0x50 -and $bytes[2] -eq 0x44 -and $bytes[3] -eq 0x46)
} catch {}
if (-not $pdfOk -and $pdf.Status -eq 200) {
    # fallback: re-fetch with curl to check magic bytes
    $out = Join-Path $env:TEMP 'adg-bill.pdf'
    & curl.exe -s -o $out -H "Authorization: Bearer $adminToken" "$ControlPlane/admin/billing/export.pdf"
    if (Test-Path $out) {
        $b = [System.IO.File]::ReadAllBytes($out)[0..3]
        $pdfOk = ($b.Length -eq 4 -and $b[0] -eq 0x25 -and $b[1] -eq 0x50 -and $b[2] -eq 0x44 -and $b[3] -eq 0x46)
        Remove-Item $out -Force -ErrorAction SilentlyContinue
    }
}
Check 'billing PDF 200 + PDF magic bytes' $pdfOk "status=$($pdf.Status)"

Write-Host ''
Write-Host '=============================================================='
Write-Host ' M8 acceptance summary'
Write-Host '=============================================================='
$script:checks | Format-Table -AutoSize
Write-Host ''
Write-Host (" Checks: {0}   PASS: {1}   FAIL: {2}" -f $script:checks.Count, $script:passed, $script:failed)
if ($script:failed -eq 0) { Write-Host ' M8 ACCEPTANCE: PASS' -ForegroundColor Green; exit 0 }
Write-Host ' M8 ACCEPTANCE: FAIL' -ForegroundColor Red
exit 1
