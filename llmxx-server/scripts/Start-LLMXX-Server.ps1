# 檔案路徑: rootmedicals-a/llmxx-server/scripts/Start-LLMXX-Server.ps1
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmxx-server 內部維運腳本，供 RootMedicals-Control 或工程診斷呼叫。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

param(
    [int]$Port = 8017,
    [switch]$NoBrowser,
    [switch]$DemoFixture,
    [switch]$SyntheticFallback
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $PSCommandPath
$ServerDir = Split-Path -Parent $ScriptDir
$DataDir = Join-Path $ServerDir "data"
$PidPath = Join-Path $DataDir "llmxx_server_$Port.pid"
$LogPath = Join-Path $DataDir "llmxx_server_$Port.out.log"
$ErrPath = Join-Path $DataDir "llmxx_server_$Port.err.log"
$HealthUrl = "http://127.0.0.1:$Port/api/health"
$DemoUrl = "http://127.0.0.1:$Port/demo/latest"

function Write-Step {
    param([string]$Message)
    Write-Host "[LLMXX] $Message"
}

function Get-ListeningProcessId {
    param([int]$TargetPort)
    $conn = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "0.0.0.0", "::", "::1") } |
        Select-Object -First 1
    if ($null -eq $conn) {
        return $null
    }
    return [int]$conn.OwningProcess
}

function Test-Health {
    param([string]$Url)
    try {
        $response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
        if ($null -eq $response) {
            return $false
        }
        if ($response.PSObject -and $null -ne $response.ok) {
            return [bool]$response.ok
        }
        $txt = [string]$response
        return $txt.Contains('"ok":true') -or $txt.Contains('"ok": true')
    } catch {
        return $false
    }
}

function Get-HealthPayload {
    param([string]$Url)
    try {
        $response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
        if ($null -ne $response -and $response -is [string]) {
            return $response | ConvertFrom-Json
        }
        return $response
    } catch {
        return $null
    }
}

function Test-PythonHasDeps {
    param([string]$Exe)
    if ([string]::IsNullOrWhiteSpace($Exe)) {
        return $false
    }
    try {
        & $Exe -c "import fastapi, uvicorn, cryptography, PIL, numpy, cv2, easyocr" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Resolve-ServerPython {
    # 依序嘗試可用的直譯器，第一個具備完整 server/OCR runtime 的就採用。
    # 交付機常見狀況：全域 python 沒裝套件、或某個 venv 是從別台機器複製過來、
    # base 直譯器路徑失效（pyvenv.cfg 指向不存在的 python）。這裡自動略過壞掉的候選。
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:LLMXX_PYTHON)) {
        $candidates += $env:LLMXX_PYTHON
    }
    $candidates += (Join-Path $ServerDir ".venv\Scripts\python.exe")
    $pathPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($pathPython) {
        $candidates += $pathPython
    }
    foreach ($cand in $candidates) {
        if ([string]::IsNullOrWhiteSpace($cand)) {
            continue
        }
        if (-not (Test-Path -LiteralPath $cand)) {
            continue
        }
        if (Test-PythonHasDeps -Exe $cand) {
            return $cand
        }
    }
    return $null
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Write-Step "Folder: $ServerDir"
Write-Step "Port  : $Port"

$ServerPython = Resolve-ServerPython
if ($null -eq $ServerPython) {
    $reqPath = Join-Path $ServerDir "requirements.txt"
    $reason = @(
        "[LLMXX] No Python interpreter with the complete server/OCR runtime was found.",
        "[LLMXX] Required: fastapi, uvicorn, cryptography, Pillow, numpy, opencv-python, easyocr.",
        "[LLMXX] Tried, in order: `$env:LLMXX_PYTHON, $ServerDir\.venv, and PATH python.",
        "[LLMXX] Fix option A: point at a working interpreter, e.g. `$env:LLMXX_PYTHON = 'C:\path\to\python.exe'.",
        "[LLMXX] Fix option B: install deps into one of the above, e.g. python -m pip install -r `"$reqPath`"."
    ) -join [Environment]::NewLine
    Write-Host $reason -ForegroundColor Red
    # 把真正的失敗原因寫進 err.log，控制台彈窗才不會顯示過期的舊 log 尾巴而誤導。
    Set-Content -LiteralPath $ErrPath -Value $reason -Encoding UTF8
    exit 1
}
Write-Step "Python: $ServerPython"

$existingPid = Get-ListeningProcessId -TargetPort $Port
if ($null -ne $existingPid) {
    Write-Step "Port $Port is already listening by PID $existingPid."
    $healthPayload = Get-HealthPayload -Url $HealthUrl
    if ($null -ne $healthPayload -and [bool]$healthPayload.ok) {
        $demoMode = ""
        if ($healthPayload.checks -and ($healthPayload.checks.PSObject.Properties.Name -contains "demo_fixture")) {
            $demoMode = [string]$healthPayload.checks.demo_fixture
        }
        if ($DemoFixture -and $demoMode -ne "enabled") {
            Write-Host "[LLMXX] Server is already running, but demo fixture mode is not enabled." -ForegroundColor Yellow
            Write-Host "[LLMXX] Use RootMedicals-Control.cmd Stop, then start again in Demo Fixture mode." -ForegroundColor Yellow
            exit 1
        }
        $syntheticMode = ""
        if ($healthPayload.checks -and ($healthPayload.checks.PSObject.Properties.Name -contains "rag_synthetic_fallback")) {
            $syntheticMode = [string]$healthPayload.checks.rag_synthetic_fallback
        }
        if ($SyntheticFallback -and $syntheticMode -ne "enabled") {
            Write-Host "[LLMXX] Server is already running, but synthetic fallback mode is not enabled." -ForegroundColor Yellow
            Write-Host "[LLMXX] Use RootMedicals-Control.cmd Stop, then start again in Live + Synthetic mode." -ForegroundColor Yellow
            exit 1
        }
        Write-Step "Health check passed. Server is already running."
        if (-not $NoBrowser) {
            Start-Process $DemoUrl
            Write-Step "Opened demo: $DemoUrl"
        }
        exit 0
    }
    Write-Host "[LLMXX] Port $Port is occupied but health check failed. Stop the other process first." -ForegroundColor Red
    exit 1
}

Remove-Item -LiteralPath $LogPath, $ErrPath -Force -ErrorAction SilentlyContinue

$arguments = @(
    "-m",
    "uvicorn",
    "server_app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    [string]$Port
)

Write-Step "Starting llmxx-server..."
$oldDemoFixture = $env:LLMXX_DEMO_FIXTURE_MODE
$oldSyntheticFallback = $env:LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK
if ($DemoFixture) {
    $env:LLMXX_DEMO_FIXTURE_MODE = "true"
    $env:LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK = "false"
    Write-Step "Demo fixture mode: enabled"
} else {
    $env:LLMXX_DEMO_FIXTURE_MODE = "false"
    $env:LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK = if ($SyntheticFallback) { "true" } else { "false" }
    Write-Step "Demo fixture mode: disabled"
}
Write-Step "RAG synthetic fallback: $(if ($SyntheticFallback -and -not $DemoFixture) { 'enabled' } else { 'disabled' })"
$process = Start-Process `
    -FilePath $ServerPython `
    -ArgumentList $arguments `
    -WorkingDirectory $ServerDir `
    -RedirectStandardOutput $LogPath `
    -RedirectStandardError $ErrPath `
    -PassThru `
    -WindowStyle Hidden
$env:LLMXX_DEMO_FIXTURE_MODE = $oldDemoFixture
$env:LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK = $oldSyntheticFallback

Set-Content -LiteralPath $PidPath -Value ([string]$process.Id) -Encoding ASCII
Write-Step "Started PID $($process.Id)."
Write-Step "Log: $LogPath"

$ready = $false
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Health -Url $HealthUrl) {
        $ready = $true
        break
    }
}

if (-not $ready) {
    Write-Host "[LLMXX] Server did not become healthy within 10 seconds." -ForegroundColor Red
    if (Test-Path -LiteralPath $ErrPath) {
        Write-Host "[LLMXX] Error log tail:" -ForegroundColor Yellow
        Get-Content -LiteralPath $ErrPath -Tail 40 -ErrorAction SilentlyContinue
    }
    exit 1
}

Write-Step "Health check passed: $HealthUrl"
if (-not $NoBrowser) {
    Start-Process $DemoUrl
    Write-Step "Opened demo: $DemoUrl"
}
Write-Step "Done. Use RootMedicals-Control.cmd Stop when you want to close the stack."

