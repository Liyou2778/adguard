# AdGuard M3 acceptance smoke test: rule-based judgment with clause-level evidence
#
# Prerequisites:
#   1) scripts\dev-up.ps1                (Redis + MinIO healthy)
#   2) MySQL (WSL instance per docs/M1-验收报告.md) + control plane on :8081
#   3) pipeline API on :8090              (python -m uvicorn adguard_pipeline.api:app --port 8090)
#   4) gateway on :8080                   (scripts\run-gateway.ps1, judge backend=rule)
#
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\smoke-m3.ps1
# Exit code 0 = all checks passed.

param(
    [string]$ApiKey = '',
    [string]$AdminKey = '',
    [string]$Gateway = 'http://127.0.0.1:8080',
    [string]$ControlPlane = 'http://127.0.0.1:8081',
    [string]$Pipeline = 'http://127.0.0.1:8090',
    [string]$Tenant = 'demo-platform',
    [int]$PollSeconds = 60
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
    if ($AdminKey) { $headers['X-Admin-Key'] = $AdminKey }
    if ($ApiKey) { $headers['X-API-Key'] = $ApiKey }
    $params = @{ Method = $Method; Uri = $Url; Headers = $headers; UseBasicParsing = $true; TimeoutSec = 60 }
    if ($null -ne $Body -and "$Body" -ne '') {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
        $params.ContentType = 'application/json; charset=utf-8'
    }
    try {
        $resp = Invoke-WebRequest @params
        return [pscustomobject]@{ Status = [int]$resp.StatusCode; Body = $resp.Content }
    } catch {
        $response = $_.Exception.Response
        $body = ''
        if ($null -ne $_.ErrorDetails -and $_.ErrorDetails.Message) {
            $body = [string]$_.ErrorDetails.Message
        } elseif ($null -ne $response) {
            try {
                $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
                $body = $reader.ReadToEnd(); $reader.Close()
            } catch { $body = '' }
        }
        $status = 0
        if ($null -ne $response) { $status = [int]$response.StatusCode }
        return [pscustomobject]@{ Status = $status; Body = $body }
    }
}

function Invoke-JsonParsed([string]$Method, [string]$Url, $Body = $null, [string]$TenantId = '') {
    $result = Invoke-Json -Method $Method -Url $Url -Body $Body -TenantId $TenantId
    $parsed = $null
    if ($result.Body) { try { $parsed = $result.Body | ConvertFrom-Json } catch { $parsed = $null } }
    return [pscustomobject]@{ Status = $result.Status; Body = $result.Body; Json = $parsed }
}

function New-TextAsset([string]$Content, [string]$AssetType = 'text') {
    # unique nonce per run so re-running the smoke test is never deduplicated away
    $Content = $Content + "`n（批次 " + [guid]::NewGuid().ToString('N').Substring(0, 12) + "）"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Content)
    $initBody = @{
        filename = 'creative.txt'
        size_bytes = $bytes.Length
        mime_type = 'text/plain; charset=utf-8'
        type = $AssetType
        chunk_size = 1048576
    }
    $init = Invoke-JsonParsed POST "$Gateway/api/v1/assets" $initBody $Tenant
    if ($init.Status -ne 201) { throw "create asset failed: $($init.Status) $($init.Body)" }
    $assetId = $init.Json.asset_id
    $tmp = Join-Path $env:TEMP ("adguard-smoke3-{0}.bin" -f ([guid]::NewGuid().ToString('N')))
    $script:tmpFiles += $tmp
    [System.IO.File]::WriteAllBytes($tmp, $bytes)
    $code = & curl.exe -s -o NUL -w "%{http_code}" -X PUT -H "X-Tenant-Id: $Tenant" -H "X-API-Key: $ApiKey" `
        -H "Content-Type: application/octet-stream" --data-binary "@$tmp" `
        "$Gateway/api/v1/assets/$assetId/chunks/0"
    if ("$code" -ne '204') { throw "upload chunk failed: $code" }
    $complete = Invoke-JsonParsed POST "$Gateway/api/v1/assets/$assetId/complete" $null $Tenant
    if ($complete.Status -ne 202) { throw "complete failed: $($complete.Status) $($complete.Body)" }
    return $assetId
}

function Wait-Judgment([string]$AssetId) {
    for ($attempt = 0; $attempt -lt $PollSeconds; $attempt++) {
        Start-Sleep -Seconds 1
        $asset = Invoke-JsonParsed GET "$Gateway/api/v1/assets/$AssetId" '' $Tenant
        if ($asset.Json) {
            if ($asset.Json.status -eq 'judged') {
                return Invoke-JsonParsed GET "$Gateway/api/v1/judgments/$($asset.Json.latest_judgment_id)" '' $Tenant
            }
            if ($asset.Json.status -eq 'failed') { throw "asset $AssetId failed judging" }
        }
    }
    throw "asset $AssetId not judged within ${PollSeconds}s"
}

Write-Host ''
Write-Host '=============================================================='
Write-Host ' AdGuard M3 acceptance smoke test (rule judge + clause evidence)'
Write-Host "  gateway=$Gateway  control-plane=$ControlPlane  pipeline=$Pipeline"
Write-Host '=============================================================='

# ---------------------------------------------------------------- 1. services + index
Write-Host ''
Write-Host '[1] Services and retrieval index'
$gw = Invoke-JsonParsed GET "$Gateway/healthz"
Check 'gateway /healthz 200' ($gw.Status -eq 200) "status=$($gw.Status)"
$pl = Invoke-JsonParsed GET "$Pipeline/healthz"
Check 'pipeline /healthz 200' ($pl.Status -eq 200) "status=$($pl.Status)"
$idx = Invoke-JsonParsed GET "$Pipeline/v1/index/status"
$clauses = if ($idx.Json) { [int]$idx.Json.clauses } else { 0 }
Check 'retrieval index has 33 clauses' ($idx.Status -eq 200 -and $clauses -eq 33) "clauses=$clauses embedder=$($idx.Json.embedder)"
Check 'retrieval index version is sha256' ($idx.Json.index_version -match '^[0-9a-f]{64}$') "version=$($idx.Json.index_version)"

# ---------------------------------------------------------------- 2. violation -> block with clause
Write-Host ''
Write-Host '[2] Violating text asset -> block with clause-level evidence'
$violationText = '全网最低价，错过再等一年！本店承诺国家级品质。'
$assetBlock = New-TextAsset $violationText
$judgment = Wait-Judgment $assetBlock
$decision = if ($judgment.Json) { $judgment.Json.decision } else { '' }
$violations = if ($judgment.Json) { @($judgment.Json.violations) } else { @() }
Check 'decision = block' ($decision -eq 'block') "decision=$decision"
Check 'judgment cites >= 1 violation' ($violations.Count -ge 1) "violations=$($violations.Count)"
if ($violations.Count -ge 1) {
    $v = $violations[0]
    Check 'violation has rule_id ADV-LAW-ART9-3' ($v.rule_id -eq 'ADV-LAW-ART9-3') "rule_id=$($v.rule_id)"
    Check 'violation has clause_id ADV-LAW-ART9-ITEM3' ($v.clause_id -eq 'ADV-LAW-ART9-ITEM3') "clause_id=$($v.clause_id)"
    Check 'violation has article 第九条 (三)' ($v.article -eq '第九条' -and $v.item_label -eq '（三）') "article=$($v.article)$($v.item_label)"
    Check 'violation has penalty range 20万-100万' ($v.penalty_min -eq 200000 -and $v.penalty_max -eq 1000000) "penalty=$($v.penalty_min)-$($v.penalty_max)"
    Check 'violation has original-text span' ($v.span_start -ge 0 -and $v.span_end -gt $v.span_start) "span=$($v.span_start)-$($v.span_end) evidence=$($v.evidence)"
    Check 'violation carries clause revision (sha256)' ($v.clause_revision -match '^[0-9a-f]{64}$') "revision=$($v.clause_revision.Substring(0,12))..."
}
$snapshot = if ($judgment.Json) { $judgment.Json.rule_snapshot_id } else { '' }
Check 'judgment bound to rule snapshot' ($snapshot -match '^[0-9a-f-]{36}$') "snapshot=$snapshot"
$modelVersion = if ($judgment.Json) { $judgment.Json.model_version } else { '' }
Check 'model_version is rule judge' ($modelVersion -like 'rule-m3-*') "model_version=$modelVersion"

# ---------------------------------------------------------------- 3. evasive spelling still blocks
Write-Host ''
Write-Host '[3] Evasive spelling (zero-width + emoji + homophone) still blocks'
# NOTE: PS 5.1 has no `u{} escape; build the string explicitly instead.
$evasiveText = "本店最" + [char]0x200B + "😀佳，醉好品质"
$assetEvasive = New-TextAsset $evasiveText
$judgmentEvasive = Wait-Judgment $assetEvasive
$decisionEvasive = if ($judgmentEvasive.Json) { $judgmentEvasive.Json.decision } else { '' }
Check 'evasive text still blocks' ($decisionEvasive -eq 'block') "decision=$decisionEvasive"

# ---------------------------------------------------------------- 4. clean text -> pass
Write-Host ''
Write-Host '[4] Compliant text -> pass'
$assetPass = New-TextAsset '本店商品支持七天无理由退换，物流售后有保障。'
$judgmentPass = Wait-Judgment $assetPass
$decisionPass = if ($judgmentPass.Json) { $judgmentPass.Json.decision } else { '' }
Check 'compliant copy -> pass' ($decisionPass -eq 'pass') "decision=$decisionPass"

# ---------------------------------------------------------------- 5. exemption hint -> manual
Write-Host ''
Write-Host '[5] Exemption-context hint -> manual (no fabricated block)'
$assetManual = New-TextAsset '销量第一（有据可查的历史数据，可提供证明）'
$judgmentManual = Wait-Judgment $assetManual
$decisionManual = if ($judgmentManual.Json) { $judgmentManual.Json.decision } else { '' }
Check 'exemption hint -> manual' ($decisionManual -eq 'manual') "decision=$decisionManual"

# ---------------------------------------------------------------- 6. image asset -> manual (OCR gap)
Write-Host ''
Write-Host '[6] Image asset -> manual (OCR not implemented until M4)'
$assetImage = New-TextAsset ('PNG' + (Get-Random)) 'image'
$judgmentImage = Wait-Judgment $assetImage
$decisionImage = if ($judgmentImage.Json) { $judgmentImage.Json.decision } else { '' }
Check 'image asset -> manual' ($decisionImage -eq 'manual') "decision=$decisionImage"

# ---------------------------------------------------------------- 7. retrieval consistency endpoint
Write-Host ''
Write-Host '[7] Retrieval endpoint consistency'
$retrieve = Invoke-JsonParsed POST "$Pipeline/v1/retrieve" @{ query = '国家级认证'; top_k = 3 }
$hit = if ($retrieve.Json -and @($retrieve.Json.hits).Count -gt 0) { @($retrieve.Json.hits)[0] } else { $null }
Check 'POST /v1/retrieve returns clause hits' ($retrieve.Status -eq 200 -and $null -ne $hit) "hits=$(@($retrieve.Json.hits).Count)"
if ($null -ne $hit) {
    Check 'retrieval hit carries revision' ($hit.revision -match '^[0-9a-f]{64}$') "clause=$($hit.clause_id)"
    Check 'retrieval reports no stale drops' ($retrieve.Json.dropped_stale -eq 0) "dropped=$($retrieve.Json.dropped_stale)"
}

foreach ($file in $script:tmpFiles) { Remove-Item $file -Force -ErrorAction SilentlyContinue }

Write-Host ''
Write-Host '=============================================================='
Write-Host ' M3 acceptance summary'
Write-Host '=============================================================='
$script:checks | Format-Table -AutoSize | Out-String -Width 160 | Write-Host
$total = $script:passed + $script:failed
Write-Host (" Checks: {0}   PASS: {1}   FAIL: {2}" -f $total, $script:passed, $script:failed)
if ($script:failed -gt 0) {
    Write-Host ' M3 ACCEPTANCE: FAIL' -ForegroundColor Red
    exit 1
}
Write-Host ' M3 ACCEPTANCE: PASS' -ForegroundColor Green
exit 0
