# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/Stop-ThinCapture-System.ps1
# 產生時間: 2026-06-17 21:35 +08:00
# 版本: v0.2-薄客戶端整理
# 說明: 關閉 thin capture tray 與 ClinicalGuard mock HIS。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 版本紀錄:
# - v0.1: 原始 demo 關閉腳本。
# - v0.2: 改為 thin capture 命名，僅停止此 ProjectRoot 內的程序。
# 安全筆記:
# - 只讀取本 app runtime 狀態檔與 Win32 process command line，不清除外部專案程序。
# ----------------------------------------------------------------------------------------------------

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$StatePath = Join-Path $RuntimeDir "thin_capture_processes.json"

function Test-ThinCaptureProcessRoot {
    param([int]$PidValue)
    try {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $PidValue"
    } catch {
        return $false
    }
    if ($null -eq $processInfo -or [string]::IsNullOrWhiteSpace([string]$processInfo.CommandLine)) {
        return $false
    }
    return ([string]$processInfo.CommandLine).IndexOf($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Stop-ThinCapturePid {
    param([int]$PidValue)
    if ($PidValue -le 0) {
        return
    }
    if (-not (Test-ThinCaptureProcessRoot -PidValue $PidValue)) {
        Write-Warning "Skipping PID=$PidValue because its command line is outside this ProjectRoot."
        return
    }
    $process = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $PidValue -Force
        Write-Host "Stopped PID=$PidValue"
    }
}

if (Test-Path -LiteralPath $StatePath) {
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $trayPid = 0
    $hisPid = 0
    if ($state.PSObject.Properties.Name -contains "capture_tray_pid") {
        $trayPid = [int]$state.capture_tray_pid
    }
    if ($state.PSObject.Properties.Name -contains "clinical_guard_pid") {
        $hisPid = [int]$state.clinical_guard_pid
    }
    Stop-ThinCapturePid -PidValue $trayPid
    Stop-ThinCapturePid -PidValue $hisPid
}

# 若狀態檔遺失或過期，仍用 ProjectRoot 限定清掉同一份交付包啟動的孤兒程序。
$patterns = @("llmxx_client_thin_capture", "clinical_guard_app.gui_app", "clinical_guard_launch.py")
foreach ($pattern in $patterns) {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
            $_.CommandLine.Contains($pattern) -and
            ([string]$_.CommandLine).IndexOf($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } | ForEach-Object {
        Stop-ThinCapturePid -PidValue ([int]$_.ProcessId)
    }
}

# 維護筆記:
# 目錄整理前的 tray 可能是用系統 Python 啟動，command line 只有
# `-m llmxx_client_thin_capture tray`，不一定帶 ProjectRoot。若不清掉，
# Ctrl+Alt+G 會出現兩個 listener 同時存在的假故障。這裡只清專案專用 package，
# 不用萬用 Python 關鍵字，避免誤殺其他工具。
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
        $_.CommandLine.Contains("llmxx_client_thin_capture") -and
        ([string]$_.CommandLine).IndexOf($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0
} | ForEach-Object {
    $pidValue = [int]$_.ProcessId
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process -and @("python", "pythonw") -contains $process.ProcessName) {
        Stop-Process -Id $pidValue -Force
        Write-Host "Stopped stale thin capture PID=$pidValue"
    }
}

if (Test-Path -LiteralPath $StatePath) {
    Remove-Item -LiteralPath $StatePath -Force
}

Write-Host "Thin Capture demo system stopped."

