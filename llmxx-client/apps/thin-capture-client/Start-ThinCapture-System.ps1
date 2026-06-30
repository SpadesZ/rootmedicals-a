# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/Start-ThinCapture-System.ps1
# 產生時間: 2026-06-25 15:35 +08:00
# 版本: v0.3-提醒視窗主題啟動
# 說明: 啟動 ClinicalGuard mock HIS 與 thin capture tray；OCR 由 llmxx-server 處理。
#       此腳本也負責把醫師提醒浮窗主題傳給 tray process，讓 demo 啟動後就能使用
#       白色、微軟黑或黑色視覺風格，不需要醫師端另外手動調整設定檔。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 版本紀錄:
# - v0.1: 原始 HIS + client 一鍵啟動腳本。
# - v0.2: 改為 thin capture client；不再檢查或啟動 client-side OCR 引擎。
# - v0.3: 新增 AlertTheme 參數，啟動時寫入 ROOTMEDICALS_ALERT_THEME 給醫師提醒浮窗使用。
# 安全筆記:
# - 只在本 app 的 runtime 目錄寫入狀態與 log，mock HIS 仍由 sibling app 啟動。
# - 隱藏 console，但保留 ClinicalGuard GUI 與 doctor alert GUI。
# - AlertTheme 只改變本機浮窗外觀，不會改變 payload、OCR、RAG、ICD gate 或最終燈號。
# ----------------------------------------------------------------------------------------------------

param(
    [switch]$DemoFixture,
    [ValidateSet("white", "microsoft_dark", "black")]
    [string]$AlertTheme = "white"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$AppsDir = (Resolve-Path -LiteralPath (Split-Path -Parent $ProjectRoot)).Path
$ClientRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $AppsDir)).Path
$RootmedicalsADir = (Resolve-Path -LiteralPath (Split-Path -Parent $ClientRoot)).Path
$ClinicalRoot = Join-Path $AppsDir "clinicalguard-standalone"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$StatePath = Join-Path $RuntimeDir "thin_capture_processes.json"
$SetupScript = Join-Path $ProjectRoot "Setup-ThinCapture-Environment.ps1"

function Resolve-PythonRuntime {
    $candidates = @()
    $localPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $localPythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
    $rootPython = Join-Path $RootmedicalsADir "venv\Scripts\python.exe"
    $rootPythonw = Join-Path $RootmedicalsADir "venv\Scripts\pythonw.exe"
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    $systemPythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue

    $candidates += [ordered]@{ Name = "client .venv"; Python = $localPython; Pythonw = $localPythonw }
    $candidates += [ordered]@{ Name = "rootmedicals-a venv"; Python = $rootPython; Pythonw = $rootPythonw }
    if ($systemPython) {
        $systemPythonwPath = if ($systemPythonw) { $systemPythonw.Source } else { $systemPython.Source }
        $candidates += [ordered]@{ Name = "system Python"; Python = $systemPython.Source; Pythonw = $systemPythonwPath }
    }

    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate.Python)) {
            continue
        }
        try {
            & $candidate.Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        $pythonw = $candidate.Pythonw
        if (-not (Test-Path -LiteralPath $pythonw)) {
            $pythonw = $candidate.Python
        }
        return [ordered]@{ Name = $candidate.Name; Python = $candidate.Python; Pythonw = $pythonw }
    }
    throw "找不到可用的 Python >= 3.10 runtime。請先執行: $SetupScript"
}

function Test-ClientDependencies {
    param([string]$Python)
    try {
        & $Python -c "import PySide6, mss, pynput, httpx, cryptography, PIL, numpy, cv2" 2>$null
    } catch {
        return $false
    }
    return ($LASTEXITCODE -eq 0)
}
if (-not (Test-Path -LiteralPath $ClinicalRoot)) {
    throw "找不到 clinicalguard-standalone: $ClinicalRoot"
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
$Runtime = Resolve-PythonRuntime
$Python = [string]$Runtime.Python
$Pythonw = [string]$Runtime.Pythonw
if (-not (Test-ClientDependencies -Python $Python)) {
    throw "$($Runtime.Name) 缺少 Thin Capture 相依套件。請先執行: $SetupScript"
}
Write-Host "使用 Python runtime: $($Runtime.Name)"

function Get-ThinCaptureProcess {
    param([Parameter(Mandatory = $true)][string]$Pattern)
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
            $_.CommandLine.Contains($Pattern) -and
            ([string]$_.CommandLine).IndexOf($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } | Select-Object -First 1
}

function Get-ClinicalGuardWindowProcess {
    param(
        [int]$ExpectedPid = 0,
        [string]$LauncherPath = ""
    )
    $windows = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.MainWindowTitle -like "*Clinical Guard (Phase 1 Local)*"
    }
    foreach ($window in $windows) {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($window.Id)" -ErrorAction SilentlyContinue
        if (-not $processInfo) {
            continue
        }
        $commandLine = [string]$processInfo.CommandLine
        if ($LauncherPath -and $commandLine.IndexOf($LauncherPath, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            continue
        }
        if ($ExpectedPid -le 0 -or $window.Id -eq $ExpectedPid -or [int]$processInfo.ParentProcessId -eq $ExpectedPid) {
            return $window
        }
    }
    return $null
}

function Wait-ClinicalGuardWindow {
    param(
        [int]$TimeoutSeconds = 30,
        [int]$ExpectedPid = 0,
        [string]$LauncherPath = ""
    )
    for ($i = 0; $i -lt ($TimeoutSeconds * 2); $i++) {
        $window = Get-ClinicalGuardWindowProcess -ExpectedPid $ExpectedPid -LauncherPath $LauncherPath
        if ($window) {
            return [int]$window.Id
        }
        Start-Sleep -Milliseconds 500
    }
    return 0
}

$ClinicalSrc = Join-Path $ClinicalRoot "src"
$ClinicalDb = Join-Path $ClinicalRoot "data\test_guard_open.db"
$ClinicalExportDir = Join-Path $ClinicalRoot "data\test_exports"
$ClinicalLauncher = Join-Path $RuntimeDir "clinical_guard_launch.py"
$ClinicalOut = Join-Path $RuntimeDir "clinical_guard.out.log"
$ClinicalErr = Join-Path $RuntimeDir "clinical_guard.err.log"
$TrayOut = Join-Path $RuntimeDir "tray_client.out.log"
$TrayErr = Join-Path $RuntimeDir "tray_client.err.log"
New-Item -ItemType Directory -Force -Path $ClinicalExportDir | Out-Null

$launcherSource = @"
import sys, types, traceback
from pathlib import Path
root = Path(r'$ClinicalRoot')
try:
    pkg = types.ModuleType('clinical_guard_app')
    pkg.__path__ = [str(root / 'src' / 'clinical_guard_app')]
    sys.modules['clinical_guard_app'] = pkg
    local_path = root / 'src' / 'clinical_guard_app' / 'local_core.py'
    local_src = local_path.read_text(encoding='utf-8').replace('return p.parents[5]', 'return p.parents[2]')
    local_mod = types.ModuleType('clinical_guard_app.local_core')
    local_mod.__file__ = str(local_path)
    local_mod.__package__ = 'clinical_guard_app'
    sys.modules['clinical_guard_app.local_core'] = local_mod
    exec(compile(local_src, str(local_path), 'exec'), local_mod.__dict__)
    gui_path = root / 'src' / 'clinical_guard_app' / 'gui_app.py'
    gui_src = gui_path.read_text(encoding='utf-8')
    gui_mod = types.ModuleType('clinical_guard_app.gui_app')
    gui_mod.__file__ = str(gui_path)
    gui_mod.__package__ = 'clinical_guard_app'
    sys.modules['clinical_guard_app.gui_app'] = gui_mod
    exec(compile(gui_src, str(gui_path), 'exec'), gui_mod.__dict__)
    raise SystemExit(gui_mod.main(['--db', r'$ClinicalDb', '--export-dir', r'$ClinicalExportDir']))
except Exception:
    (root / 'data' / 'gui_launch_error.log').write_text(traceback.format_exc(), encoding='utf-8')
    raise
"@
Set-Content -LiteralPath $ClinicalLauncher -Value $launcherSource -Encoding UTF8

$hisProcessInfo = Get-ThinCaptureProcess -Pattern "clinical_guard_launch.py"
if (-not $hisProcessInfo) {
    $hisProcessInfo = Get-ThinCaptureProcess -Pattern "clinical_guard_app.gui_app"
}
if ($hisProcessInfo) {
    $hisPid = [int]$hisProcessInfo.ProcessId
    Write-Host "clinicalguard-standalone 已在執行。PID=$hisPid"
} else {
    # mock HIS 是獨立 sibling app；這裡用 runtime shim 啟動，避免直接改 ClinicalGuard 原始碼。
    $oldPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $ClinicalSrc
    $hisArgs = @("`"$ClinicalLauncher`"")
    Remove-Item -LiteralPath $ClinicalOut, $ClinicalErr -Force -ErrorAction SilentlyContinue
    # 維護筆記:
    # ClinicalGuard 是可見 GUI，但啟動過程仍可能因 import、Tk 或 sync endpoint 出錯。
    # 用 Start-Process 將 stdout/stderr 寫入 runtime log，控制器失敗時才有線索可追。
    $his = Start-Process `
        -FilePath $Python `
        -ArgumentList $hisArgs `
        -WorkingDirectory $ClinicalRoot `
        -RedirectStandardOutput $ClinicalOut `
        -RedirectStandardError $ClinicalErr `
        -WindowStyle Hidden `
        -PassThru
    $env:PYTHONPATH = $oldPythonPath
    $hisPid = [int]$his.Id
    Write-Host "clinicalguard-standalone 已啟動。PID=$hisPid"
}

$hisWindowPid = Wait-ClinicalGuardWindow -TimeoutSeconds 30 -ExpectedPid $hisPid -LauncherPath $ClinicalLauncher
if ($hisWindowPid -le 0) {
    if (Test-Path -LiteralPath $ClinicalErr) {
        Write-Host "Clinical Guard stderr:" -ForegroundColor Yellow
        Get-Content -LiteralPath $ClinicalErr -Tail 30 -ErrorAction SilentlyContinue
    }
    throw "Clinical Guard 視窗啟動後仍不可見。若存在，請查看 $ClinicalRoot\data\gui_launch_error.log。"
}

$trayProcessInfo = Get-ThinCaptureProcess -Pattern "llmxx_client_thin_capture"
if ($trayProcessInfo) {
    $trayPid = [int]$trayProcessInfo.ProcessId
    Write-Host "thin capture tray client 已在執行。PID=$trayPid"
    Write-Host "既有 tray process 不會套用新的 alert theme。若要換主題，請先 Stop 再重新啟動。" -ForegroundColor Yellow
    if ($DemoFixture) {
        Write-Host "已要求 DemoFixture，但既有 tray process 無法繼承新的 demo marker。" -ForegroundColor Yellow
        Write-Host "請先用 RootMedicals-Control.cmd Stop，再重新啟動指定模式。" -ForegroundColor Yellow
    }
} else {
    # tray 留在背景；Ctrl+Alt+G 只負責截圖與送 server，server 才執行 OCR/RAG。
    $oldPythonPath = $env:PYTHONPATH
    $oldDemoFixture = $env:LLMXX_CLIENT_DEMO_FIXTURE_ENABLED
    $oldAlertTheme = $env:ROOTMEDICALS_ALERT_THEME
    $env:PYTHONPATH = Join-Path $ProjectRoot "src"
    $env:ROOTMEDICALS_ALERT_THEME = $AlertTheme
    if ($DemoFixture) {
        $env:LLMXX_CLIENT_DEMO_FIXTURE_ENABLED = "true"
        Write-Host "Demo fixture payload marker: 已啟用"
    } else {
        $env:LLMXX_CLIENT_DEMO_FIXTURE_ENABLED = "false"
    }
    Remove-Item -LiteralPath $TrayOut, $TrayErr -Force -ErrorAction SilentlyContinue
    $tray = Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "llmxx_client_thin_capture", "tray") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $TrayOut `
        -RedirectStandardError $TrayErr `
        -WindowStyle Hidden `
        -PassThru
    $env:PYTHONPATH = $oldPythonPath
    $env:LLMXX_CLIENT_DEMO_FIXTURE_ENABLED = $oldDemoFixture
    $env:ROOTMEDICALS_ALERT_THEME = $oldAlertTheme
    $trayPid = [int]$tray.Id
    Write-Host "thin capture tray client 已啟動。PID=$trayPid, alert theme=$AlertTheme"
}

$state = [ordered]@{
    started_at = (Get-Date).ToString("s")
    project_root = $ProjectRoot
    clinical_guard_pid = $hisPid
    clinical_guard_window_pid = $hisWindowPid
    capture_tray_pid = $trayPid
    trigger = "Ctrl+Alt+G / tray double-click / tray menu"
    diagnostics_location = "diagnostics\YYYYMMDD\diagnostics_*.json"
    demo_fixture_payload_marker = [bool]$DemoFixture
    alert_theme = $AlertTheme
}
$state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding UTF8

Write-Host ""
Write-Host "Demo 系統已就緒。"
Write-Host "1. 在 Clinical Guard 輸入 SOAP 與 Vital Signs。"
Write-Host "2. 用 Ctrl+Alt+G 或 tray icon 觸發 thin capture。"
Write-Host "3. llmxx-server 會執行 OCR/RAG，並回傳醫師端提醒結果。"
Write-Host "4. diagnostics\YYYYMMDD\diagnostics_*.json 僅供除錯；其中截圖內容會被遮蔽。"


