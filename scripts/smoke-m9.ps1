# M9 (FR-13) acceptance smoke: per-tenant quota enforcement (429), usage ledger/cost, billing exports.
param(
    [string]$Gateway = 'http://127.0.0.1:8080',
    [string]$ControlPlane = 'http://127.0.0.1:8081',
    [string]$AdminUser = 'admin',
    [string]$AdminPassword = '',
    [string]$AdminKey = '',
    [string]$ApiKey = '',
    [int]$PollSeconds = 45
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
    if ($TenantHeader) { $headers['X-Tenant-Id'] = $TenantHeader }
    if ($AdminKey -and -not $Anonymous) { $headers['X-Admin-Key'] = $AdminKey }
    if ($ApiKey -and -not $Anonymous) { $headers['X-API-Key'] = $ApiKey }
    $resp = $null; $content = ''
    try {
        $params = @{ Uri = $Url; Method = $Method; Headers = $headers; UseBasicParsing = $true; TimeoutSec = 60 }
        if ($null -ne $Body) { $params['ContentType'] = 'application/json; charset=utf-8'; $params['Body'] = ($Body | ConvertTo-Json -Depth 10 -Compress) }
        $resp = Invoke-WebRequest @params
        $content = $resp.Content
    } catch {
        $resp = $_.Exception.Response
        $content = ''
        # PowerShell 5.1 does not expose the body through GetResponseStream() for failed
        # requests; ErrorDetails.Message is the reliable channel (same handling as smoke-m1).
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
function Create-Asset([string]$Tenant, [string]$Type = 'text', [int]$Size = 40) {
    $body = @{ filename = 'q.txt'; size_bytes = $Size; mime_type = 'text/plain'; type = $Type; chunk_size = 1048576 }
    return Invoke-Api POST "$Gateway/api/v1/assets" $body '' $Tenant
}
function Upload-AndJudge([string]$Tenant, [string]$Content) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Content)
    $body = @{ filename = 'usage.txt'; size_bytes = $bytes.Length; mime_type = 'text/plain'; type = 'text'; chunk_size = 1048576 }
    $init = Invoke-Api POST "$Gateway/api/v1/assets" $body '' $Tenant
    if ($init.Status -ne 201) { return $null }
    $assetId = $init.Json.asset_id
    $tmp = Join-Path $env:TEMP "m9-$assetId.bin"
    [System.IO.File]::WriteAllBytes($tmp, $bytes)
    & curl.exe -s -o NUL -X PUT -H "X-Tenant-Id: $Tenant" -H "X-API-Key: $ApiKey" -H 'Content-Type: application/octet-stream' --data-binary "@$tmp" "$Gateway/api/v1/assets/$assetId/chunks/0" | Out-Null
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    $complete = Invoke-Api POST "$Gateway/api/v1/assets/$assetId/complete" $null '' $Tenant
    if ($complete.Status -ne 202 -and $complete.Status -ne 200) { return $null }
    for ($i = 0; $i -lt $PollSeconds; $i++) {
        Start-Sleep -Seconds 1
        $asset = Invoke-Api GET "$Gateway/api/v1/assets/$assetId" $null '' $Tenant
        if ($asset.Json -and $asset.Json.latest_judgment_id) {
            $judgment = Invoke-Api GET "$Gateway/api/v1/judgments/$($asset.Json.latest_judgment_id)" $null '' $Tenant
            return [pscustomobject]@{ AssetId = $assetId; Judgment = $judgment.Json }
        }
        if ($asset.Json -and $asset.Json.status -eq 'failed') { return $null }
    }
    return $null
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' AdGuard M9 acceptance smoke (quota + usage + billing)'
Write-Host "  gateway=$Gateway control-plane=$ControlPlane"
Write-Host '=============================================================='

Write-Host ''
Write-Host '[1] Service liveness + admin login'
Check 'gateway /healthz 200' ((Invoke-Api GET "$Gateway/healthz").Status -eq 200) ''
Check 'control-plane health 200' ((Invoke-Api GET "$ControlPlane/actuator/health").Status -eq 200) ''
$login = Invoke-Api POST "$ControlPlane/auth/login" @{ username = $AdminUser; password = $AdminPassword }
$adminToken = if ($login.Json) { $login.Json.access_token } else { '' }
Check 'admin login 200 + JWT' ($login.Status -eq 200 -and $adminToken -ne '') "status=$($login.Status)"

Write-Host ''
Write-Host '[2] Provision tenant with a 3-point daily quota'
$tenant = 'quota-' + (Get-Date -Format 'HHmmss')
$tenantResp = Invoke-Api POST "$ControlPlane/admin/tenants" @{ tenant_id = $tenant; name = 'Quota Test'; daily_quota = 3 } $adminToken
Check 'create tenant (quota=3) 201' ($tenantResp.Status -eq 201 -and $tenantResp.Json.daily_quota -eq 3) "status=$($tenantResp.Status)"
$username = 'quser-' + (Get-Date -Format 'HHmmss')
$userResp = Invoke-Api POST "$ControlPlane/admin/users" @{ username = $username; password = 'QuotaPass123!'; role = 'owner'; tenant_id = $tenant } $adminToken
Check 'create tenant owner 201' ($userResp.Status -eq 201) "status=$($userResp.Status)"

Write-Host ''
Write-Host '[3] Weighted quota: text weight=1, quota=3 -> 3 allowed, 4th rejected'
$allowed = 0
for ($i = 1; $i -le 3; $i++) {
    $created = Create-Asset $tenant 'text' 40
    if ($created.Status -eq 201) { $allowed++ } else { Write-Host "  create $i status=$($created.Status) $($created.Body)" }
}
Check 'first 3 submissions allowed' ($allowed -eq 3) "allowed=$allowed"
$overflow = Create-Asset $tenant 'text' 40
Check '4th submission -> 429' ($overflow.Status -eq 429) "status=$($overflow.Status)"
$overflowRaw = Invoke-Api POST "$Gateway/api/v1/assets" @{ filename = 'q.txt'; size_bytes = 40; mime_type = 'text/plain'; type = 'text'; chunk_size = 1048576 } '' $tenant -Raw
$retryAfter = ''
if ($overflowRaw.Raw) { try { $retryAfter = "$($overflowRaw.Raw.Headers['Retry-After'])" } catch {} }
Check '429 carries Retry-After' ($retryAfter -ne '') "retry_after=$retryAfter"
Check 'error code QUOTA_EXCEEDED' ("$($overflow.Body)" -match 'QUOTA_EXCEEDED') ''

Write-Host ''
Write-Host '[4] Disabled tenant is rejected'
$disable = Invoke-Api PATCH "$ControlPlane/admin/tenants/$tenant" @{ status = 'disabled' } $adminToken
Check 'disable tenant 200' ($disable.Status -eq 200) "status=$($disable.Status)"
$rejected = Create-Asset $tenant 'text' 40
Check 'disabled tenant -> 403 TENANT_DISABLED' ($rejected.Status -eq 403 -and "$($rejected.Body)" -match 'TENANT_DISABLED') "status=$($rejected.Status)"
$enable = Invoke-Api PATCH "$ControlPlane/admin/tenants/$tenant" @{ status = 'active'; daily_quota = 5 } $adminToken
Check 're-enable + quota raise to 5' ($enable.Status -eq 200 -and $enable.Json.daily_quota -eq 5) "quota=$($enable.Json.daily_quota)"

Write-Host ''
Write-Host '[5] Judgment writes the usage ledger + cost'
$today = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
$month = (Get-Date).ToUniversalTime().ToString('yyyy-MM')
$judged = Upload-AndJudge 'demo-platform' ('全网最低价 usage probe ' + (Get-Date -Format 'HHmmssfff'))
Check 'demo-platform asset judged' ($null -ne $judged) ''
$cost = 0
if ($judged -and $judged.Judgment) { $cost = [int64]$judged.Judgment.cost_micro }
Check 'judgment carries cost_micro (text=100)' ($cost -eq 100) "cost_micro=$cost"
$usage = Invoke-Api GET "$Gateway/api/v1/usage?from=$today&to=$today" $null '' 'demo-platform'
$usageOk = $false; $usageCalls = 0; $usageCost = 0
if ($usage.Json) {
    $usageCalls = [int64]$usage.Json.calls; $usageCost = [int64]$usage.Json.cost_micro
    $usageOk = ($usageCalls -ge 1 -and $usageCost -ge 100)
}
Check 'GET /api/v1/usage reflects call + cost' $usageOk "calls=$usageCalls cost_micro=$usageCost status=$($usage.Status)"

Write-Host ''
Write-Host '[6] Billing summary + exports'
$summary = Invoke-Api GET "$ControlPlane/admin/billing/summary?month=$month" $null $adminToken
$summaryOk = $false
if ($summary.Json) {
    $summaryOk = ([int64]$summary.Json.total_cost_micro -ge 100)
    if (-not $summaryOk) {
        foreach ($t in $summary.Json.tenants) { if ($t.tenant_id -eq 'demo-platform' -and [int64]$t.cost_micro -ge 100) { $summaryOk = $true } }
    }
}
Check 'billing summary includes cost' ($summary.Status -eq 200 -and $summaryOk) "status=$($summary.Status)"
$csv = Invoke-Api GET "$ControlPlane/admin/billing/export.csv?month=$month" $null $adminToken -Raw
Check 'CSV export 200 + BOM + row' ($csv.Status -eq 200 -and $csv.Body.StartsWith([char]0xFEFF) -and $csv.Body -match 'demo-platform') ''
$out = Join-Path $env:TEMP 'm9-bill.pdf'
& curl.exe -s -o $out -H "Authorization: Bearer $adminToken" "$ControlPlane/admin/billing/export.pdf?month=$month"
$pdfOk = $false
if (Test-Path $out) {
    $b = [System.IO.File]::ReadAllBytes($out)
    $pdfOk = ($b.Length -gt 1000 -and $b[0] -eq 0x25 -and $b[1] -eq 0x50 -and $b[2] -eq 0x44 -and $b[3] -eq 0x46)
}
Remove-Item $out -Force -ErrorAction SilentlyContinue
Check 'PDF monthly bill valid' $pdfOk ''

Write-Host ''
Write-Host '=============================================================='
Write-Host ' M9 acceptance summary'
Write-Host '=============================================================='
$script:checks | Format-Table -AutoSize
Write-Host (" Checks: {0}   PASS: {1}   FAIL: {2}" -f $script:checks.Count, $script:passed, $script:failed)
if ($script:failed -eq 0) { Write-Host ' M9 ACCEPTANCE: PASS' -ForegroundColor Green; exit 0 }
Write-Host ' M9 ACCEPTANCE: FAIL' -ForegroundColor Red
exit 1
