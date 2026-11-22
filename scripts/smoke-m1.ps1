# AdGuard M1 acceptance smoke test (real MySQL + Redis + MinIO stack)
#
# Prerequisites:
#   1) scripts\dev-up.ps1            (middleware stack healthy)
#   2) scripts\run-control-plane.ps1 (control plane on :8081)
#   3) scripts\run-gateway.ps1       (gateway on :8080)
#
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\smoke-m1.ps1
# Exit code 0 = all checks passed.

param(
    [string]$Gateway = 'http://127.0.0.1:8080',
    [string]$ControlPlane = 'http://127.0.0.1:8081',
    [string]$Tenant = 'demo-platform',
    [int]$ChunkSize = 5242880,
    [int]$PollSeconds = 40
)

$ErrorActionPreference = 'Stop'
$script:passed = 0
$script:failed = 0
$script:checks = @()
$script:tmpFiles = @()

function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { $script:passed++ ; $status = 'PASS' } else { $script:failed++ ; $status = 'FAIL' }
    $script:checks += [pscustomobject]@{ Check = $Name; Status = $status; Detail = $Detail }
    $color = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}  {2}" -f $status, $Name, $Detail) -ForegroundColor $color
}

function Invoke-Json([string]$Method, [string]$Url, $Body = $null, [string]$TenantId = '') {
    $headers = @{}
    if ($TenantId) { $headers['X-Tenant-Id'] = $TenantId }
    $params = @{ Method = $Method; Uri = $Url; Headers = $headers; UseBasicParsing = $true; TimeoutSec = 60 }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
        $params.ContentType = 'application/json; charset=utf-8'
    }
    try {
        $resp = Invoke-WebRequest @params
        return [pscustomobject]@{ Status = [int]$resp.StatusCode; Body = $resp.Content }
    } catch {
        $response = $_.Exception.Response
        if ($null -eq $response) { throw }
        $stream = $response.GetResponseStream()
        $text = (New-Object System.IO.StreamReader($stream)).ReadToEnd()
        return [pscustomobject]@{ Status = [int]$response.StatusCode; Body = $text }
    }
}

function Invoke-JsonParsed([string]$Method, [string]$Url, $Body = $null, [string]$TenantId = '') {
    $result = Invoke-Json -Method $Method -Url $Url -Body $Body -TenantId $TenantId
    $parsed = $null
    if ($result.Body) {
        try { $parsed = $result.Body | ConvertFrom-Json } catch { $parsed = $null }
    }
    return [pscustomobject]@{ Status = $result.Status; Body = $result.Body; Json = $parsed }
}

function New-ChunkFile([string]$Path, [byte[]]$Bytes, [int]$Offset, [int]$Length) {
    $slice = New-Object byte[] $Length
    [Array]::Copy($Bytes, $Offset, $slice, 0, $Length)
    [System.IO.File]::WriteAllBytes($Path, $slice)
    return $Path
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' AdGuard M1 acceptance smoke test'
Write-Host "  gateway=$Gateway  control-plane=$ControlPlane  tenant=$Tenant"
Write-Host '=============================================================='

# ---------------------------------------------------------------- 1. liveness
Write-Host ''
Write-Host '[1] Service liveness'
$gw = Invoke-JsonParsed GET "$Gateway/healthz"
Check 'gateway /healthz 200' ($gw.Status -eq 200) "status=$($gw.Status)"

$cp = Invoke-JsonParsed GET "$ControlPlane/actuator/health"
$cpStatus = if ($cp.Json) { $cp.Json.status } else { 'n/a' }
Check 'control plane /actuator/health 200' ($cp.Status -eq 200) "status=$($cp.Status) health=$cpStatus"

# ---------------------------------------------------------------- 2. rule + snapshot
Write-Host ''
Write-Host '[2] Rule library + immutable snapshot (Java control plane)'
$ruleId = 'ADV-SMOKE-' + (Get-Date -Format 'yyyyMMddHHmmss')
$rule = @{
    rule_id = $ruleId
    law = '中华人民共和国广告法'
    article = '第九条'
    item = '（三）'
    family = 'absolute_terms'
    severity = 'high'
    text = '不得使用“国家级”“最高级”“最佳”等用语'
    canonical_terms = @('最好', '最佳', '最高级', '国家级')
    detection = @{
        lexical = @('regex:最[好佳优低高强]')
        semantic = '判断该表述是否构成对商品性能的绝对化宣称'
        context_exemptions = @('有据可查的历史事实需附证明')
    }
    penalty = @{ law_ref = '第五十七条'; min = 200000; max = 1000000 }
    effective_from = '2015-09-01'
    source_url = 'https://example.gov.cn/ad-law'
}
$created = Invoke-JsonParsed POST "$ControlPlane/admin/rules" $rule $Tenant
Check 'POST /admin/rules 201' ($created.Status -eq 201) "status=$($created.Status) rule_id=$ruleId"

$activated = Invoke-JsonParsed POST "$ControlPlane/admin/rules/$ruleId/activate" $null $Tenant
$snapshotId = if ($activated.Json) { $activated.Json.id } else { '' }
$checksum = if ($activated.Json) { $activated.Json.checksum } else { '' }
Check 'POST /admin/rules/{id}/activate 200' ($activated.Status -eq 200) "status=$($activated.Status) snapshot=$snapshotId"
Check 'snapshot checksum is sha256 (64 hex)' ($checksum -match '^[0-9a-f]{64}$') "checksum=$checksum"
Check 'snapshot payload contains the activated rule' ($activated.Body -match [regex]::Escape($ruleId)) ''

$latest = Invoke-JsonParsed GET "$ControlPlane/internal/rules/snapshot?tenant_id=$Tenant"
$latestId = if ($latest.Json) { $latest.Json.id } else { '' }
Check 'GET /internal/rules/snapshot returns latest snapshot' ($latest.Status -eq 200 -and $latestId -eq $snapshotId) "latest=$latestId"

# ---------------------------------------------------------------- 3. upload closed loop
Write-Host ''
Write-Host '[3] Upload -> object storage -> async judgment (Go gateway)'
$size = 11 * 1024 * 1024
$bytes = New-Object byte[] $size
$rand = New-Object System.Random 20261122
$rand.NextBytes($bytes)
$expectedDigest = (Get-FileHash -InputStream (New-Object System.IO.MemoryStream(,$bytes)) -Algorithm SHA256).Hash.ToLower()

$initBody = @{
    filename = '双11大促主图.png'
    size_bytes = $size
    mime_type = 'image/png'
    type = 'image'
    chunk_size = $ChunkSize
}
$init = Invoke-JsonParsed POST "$Gateway/api/v1/assets" $initBody $Tenant
$assetId = if ($init.Json) { $init.Json.asset_id } else { '' }
$totalChunks = if ($init.Json) { [int]$init.Json.total_chunks } else { 0 }
Check 'POST /api/v1/assets 201' ($init.Status -eq 201) "asset=$assetId chunks=$totalChunks"
Check 'chunk count computed from size' ($totalChunks -eq [math]::Ceiling($size / $ChunkSize)) "expected=$([math]::Ceiling($size / $ChunkSize))"

for ($i = 0; $i -lt $totalChunks; $i++) {
    $offset = $i * $ChunkSize
    $length = [math]::Min($ChunkSize, $size - $offset)
    $tmp = Join-Path $env:TEMP ("adguard-chunk-{0}-{1}.bin" -f $i, $PID)
    $script:tmpFiles += $tmp
    New-ChunkFile $tmp $bytes $offset $length | Out-Null
    $code = & curl.exe -s -o NUL -w "%{http_code}" -X PUT -H "X-Tenant-Id: $Tenant" `
        -H "Content-Type: application/octet-stream" --data-binary "@$tmp" `
        "$Gateway/api/v1/assets/$assetId/chunks/$i"
    Check "PUT chunk $i/$($totalChunks - 1) 204" ("$code" -eq '204') "http=$code bytes=$length"
}
# idempotent chunk retry: re-uploading chunk 0 must not increase the counter
$chunk0 = $script:tmpFiles[0]
$headers = & curl.exe -s -D - -o NUL -X PUT -H "X-Tenant-Id: $Tenant" `
    -H "Content-Type: application/octet-stream" --data-binary "@$chunk0" `
    "$Gateway/api/v1/assets/$assetId/chunks/0"
$receivedAtRetry = ($headers | Select-String -Pattern 'X-Chunks-Received:\s*(\d+)').Matches.Groups[1].Value
Check 'chunk retry is idempotent' ($receivedAtRetry -eq "$totalChunks") "received=$receivedAtRetry"

$complete = Invoke-JsonParsed POST "$Gateway/api/v1/assets/$assetId/complete" $null $Tenant
$digest = if ($complete.Json) { $complete.Json.digest } else { '' }
Check 'POST /complete 202 (queued)' ($complete.Status -eq 202) "status=$($complete.Status) state=$($complete.Json.status)"
Check 'server digest == client digest' ($digest -eq $expectedDigest) "server=$digest"
Check 'merged object key present' ($complete.Body -match 'digest') ''

$judged = $false
$judgmentId = ''
$assetStatus = ''
for ($attempt = 0; $attempt -lt $PollSeconds; $attempt++) {
    Start-Sleep -Seconds 1
    $asset = Invoke-JsonParsed GET "$Gateway/api/v1/assets/$assetId" '' $Tenant
    if ($asset.Json) {
        $assetStatus = $asset.Json.status
        if ($asset.Json.latest_judgment_id) { $judgmentId = $asset.Json.latest_judgment_id }
        if ($assetStatus -eq 'judged') { $judged = $true; break }
        if ($assetStatus -eq 'failed') { break }
    }
}
Check 'asset reaches judged state (async worker)' $judged "status=$assetStatus after ${PollSeconds}s max"

if ($judged) {
    $judgment = Invoke-JsonParsed GET "$Gateway/api/v1/judgments/$judgmentId" '' $Tenant
    $decision = if ($judgment.Json) { $judgment.Json.decision } else { '' }
    $snapshotBound = if ($judgment.Json) { $judgment.Json.rule_snapshot_id } else { '' }
    Check 'GET /judgments/{id} 200' ($judgment.Status -eq 200) "status=$($judgment.Status)"
    Check 'decision = manual (M1 stub, ADR-2)' ($decision -eq 'manual') "decision=$decision"
    Check 'judgment bound to rule snapshot' ($snapshotBound -eq $snapshotId) "snapshot=$snapshotBound"
    Check 'violations is an empty array (not null)' ($judgment.Body -match '"violations"\s*:\s*\[\s*\]') ''
    Check 'model_version recorded' ($judgment.Body -match '"model_version"\s*:\s*"stub') ''
}

# ---------------------------------------------------------------- 4. dedupe
Write-Host ''
Write-Host '[4] Content dedupe (same tenant)'
$init2 = Invoke-JsonParsed POST "$Gateway/api/v1/assets" $initBody $Tenant
$assetId2 = if ($init2.Json) { $init2.Json.asset_id } else { '' }
for ($i = 0; $i -lt $totalChunks; $i++) {
    $tmp = $script:tmpFiles[$i]
    $code = & curl.exe -s -o NUL -w "%{http_code}" -X PUT -H "X-Tenant-Id: $Tenant" `
        -H "Content-Type: application/octet-stream" --data-binary "@$tmp" `
        "$Gateway/api/v1/assets/$assetId2/chunks/$i"
    if ("$code" -ne '204') { Check "dedupe upload chunk $i" $false "http=$code" }
}
$complete2 = Invoke-JsonParsed POST "$Gateway/api/v1/assets/$assetId2/complete" $null $Tenant
$dedupedAsset = if ($complete2.Json) { $complete2.Json.asset_id } else { '' }
$isDeduped = if ($complete2.Json) { $complete2.Json.deduped } else { $false }
Check 'duplicate upload returns existing asset' ($complete2.Status -eq 200 -and $isDeduped -eq $true -and $dedupedAsset -eq $assetId) `
    "status=$($complete2.Status) deduped=$isDeduped asset=$dedupedAsset"

# ---------------------------------------------------------------- 5. isolation & guards
Write-Host ''
Write-Host '[5] Tenant isolation and input guards'
$crossTenant = Invoke-Json GET "$Gateway/api/v1/assets/$assetId" '' 'other-tenant'
Check 'cross-tenant asset read -> 404' ($crossTenant.Status -eq 404) "status=$($crossTenant.Status)"

$noTenant = Invoke-Json GET "$Gateway/api/v1/assets/$assetId"
Check 'missing X-Tenant-Id -> 400' ($noTenant.Status -eq 400 -and $noTenant.Body -match 'MISSING_TENANT') "status=$($noTenant.Status)"

$badType = Invoke-Json POST "$Gateway/api/v1/assets" @{ filename = 'a.png'; size_bytes = 100; type = 'audio' } $Tenant
Check 'invalid asset type -> 400' ($badType.Status -eq 400 -and $badType.Body -match 'INVALID_ASSET_TYPE') "status=$($badType.Status)"

# ---------------------------------------------------------------- 6. download + metrics
Write-Host ''
Write-Host '[6] Download URL and metrics'
$download = Invoke-JsonParsed GET "$Gateway/api/v1/assets/$assetId/download-url" '' $Tenant
$url = if ($download.Json) { $download.Json.url } else { '' }
Check 'GET /download-url 200' ($download.Status -eq 200 -and $url -match '^https?://') "url_present=$([bool]$url)"

$metrics = Invoke-Json GET "$Gateway/metrics"
Check 'GET /metrics 200' ($metrics.Status -eq 200) "status=$($metrics.Status)"
Check 'gateway metrics exposed' ($metrics.Body -match 'adguard_http_requests_total' -and $metrics.Body -match 'adguard_judgments_total') ''

# ---------------------------------------------------------------- cleanup + report
foreach ($file in $script:tmpFiles) { Remove-Item $file -Force -ErrorAction SilentlyContinue }

Write-Host ''
Write-Host '=============================================================='
Write-Host ' M1 acceptance summary'
Write-Host '=============================================================='
$script:checks | Format-Table -AutoSize | Out-String -Width 160 | Write-Host
$total = $script:passed + $script:failed
Write-Host (" Checks: {0}   PASS: {1}   FAIL: {2}" -f $total, $script:passed, $script:failed)
if ($script:failed -gt 0) {
    Write-Host ' M1 ACCEPTANCE: FAIL' -ForegroundColor Red
    exit 1
}
Write-Host ' M1 ACCEPTANCE: PASS' -ForegroundColor Green
exit 0
