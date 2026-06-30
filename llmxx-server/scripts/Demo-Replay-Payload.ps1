# 檔案路徑: rootmedicals-a/llmxx-server/scripts/Demo-Replay-Payload.ps1
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmxx-server 內部維運腳本，供 RootMedicals-Control 或工程診斷呼叫。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

param(
    [string]$EndpointUrl = "http://127.0.0.1:8017/api/intake",
    [string]$PayloadPath = "",
    [switch]$NewSessionId,
    [switch]$OpenDemo,
    [int]$TimeoutSeconds = 12
)

$ErrorActionPreference = "Stop"

function Get-RootmedicalsADir {
    $scriptRoot = Split-Path -Parent $PSCommandPath
    $serverRoot = Split-Path -Parent $scriptRoot
    return Split-Path -Parent $serverRoot
}

function Test-UsablePayload {
    param([object]$Payload)
    if ($null -eq $Payload) {
        return $false
    }
    if ($Payload.schema_version -ne "llmxx-client-local-ocr.v0.1") {
        return $false
    }
    if ($null -eq $Payload.soap) {
        return $false
    }
    $assessment = [string]($Payload.soap.A)
    $plan = [string]($Payload.soap.P)
    return (($assessment.Trim().Length -gt 0) -or ($plan.Trim().Length -gt 0))
}

function Get-LatestUsablePayloadPath {
    $rootmedicalsA = Get-RootmedicalsADir
    $payloadRoot = Join-Path $rootmedicalsA "runtime_reports\archived-thin-capture-client-generated\server_payloads"
    if (-not (Test-Path -LiteralPath $payloadRoot)) {
        throw "Payload root not found: $payloadRoot"
    }

    $files = Get-ChildItem -LiteralPath $payloadRoot -Recurse -Filter "server_payload_*.json" |
        Sort-Object FullName -Descending
    foreach ($file in $files) {
        try {
            $payload = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
            if (Test-UsablePayload -Payload $payload) {
                return $file.FullName
            }
        } catch {
            Write-Warning "Skipping unreadable payload: $($file.FullName)"
        }
    }
    if ($files.Count -gt 0) {
        return $files[0].FullName
    }
    throw "No server_payload_*.json files found under $payloadRoot"
}

function ConvertTo-BodyJson {
    param([object]$Payload)
    if ($NewSessionId) {
        $stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddHHmmssfff")
        $Payload.session_id = "replay-$stamp"
        $Payload.created_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    return $Payload | ConvertTo-Json -Depth 32
}

if ([string]::IsNullOrWhiteSpace($PayloadPath)) {
    $PayloadPath = Get-LatestUsablePayloadPath
}

if (-not (Test-Path -LiteralPath $PayloadPath)) {
    throw "PayloadPath not found: $PayloadPath"
}

$payload = Get-Content -LiteralPath $PayloadPath -Raw | ConvertFrom-Json
$bodyJson = ConvertTo-BodyJson -Payload $payload
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($bodyJson)

Write-Host "[Replay] Endpoint: $EndpointUrl"
Write-Host "[Replay] Payload : $PayloadPath"
Write-Host "[Replay] Session : $($payload.session_id)"

try {
    $response = Invoke-RestMethod `
        -Method Post `
        -Uri $EndpointUrl `
        -Body $bodyBytes `
        -ContentType "application/json; charset=utf-8" `
        -TimeoutSec $TimeoutSeconds
} catch {
    if ($_.Exception.Response -and $_.ErrorDetails.Message) {
        Write-Host "[Replay] Server returned non-2xx response:"
        Write-Host $_.ErrorDetails.Message
    }
    throw
}

$summary = [ordered]@{
    ok = $response.ok
    status = $response.status
    session_id = $response.session_id
    client_session_id = $response.client_session_id
    error_code = $response.error_code
    final_light_color = $response.final_gate.light_color
    display_mode = $response.final_gate.display_mode
    evidence_backed = $response.final_gate.evidence_backed
    events_url = $response.events_url
}

Write-Host "[Replay] Response summary:"
$summary | ConvertTo-Json -Depth 8

if ($OpenDemo) {
    $demoUrl = $EndpointUrl -replace "/api/intake$", "/demo/latest"
    Start-Process $demoUrl
    Write-Host "[Replay] Demo viewer opened: $demoUrl"
}

