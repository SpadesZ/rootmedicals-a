# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/Setup-ThinCapture-Environment.ps1
# 產生時間: 2026-06-17 21:35 +08:00
# 版本: v0.2-薄客戶端整理
# 說明: 建立或修復 thin capture client 的本機 Python 虛擬環境。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 版本紀錄:
# - v0.1: 原始環境建立腳本。
# - v0.2: 改為 thin capture 套件，依賴不再包含 client-side OCR。
# 安全筆記:
# - 僅寫入本 app 的 .venv 與 Python package metadata，不修改 HIS、server、API key 或病歷資料。
# 驗證方式:
# - 建立後用 `python -m llmxx_client_thin_capture --help` 與 Start-ThinCapture-System.ps1 前置檢查確認。
# ----------------------------------------------------------------------------------------------------

param(
    [switch]$PdfFull,
    [switch]$ForceRecreate
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$VenvDir = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "[ThinCapture Setup] $Message"
}

function Resolve-BasePython {
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3.10 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @("py", "-3.10")
        }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @($python.Source)
        }
    }

    throw "Python >= 3.10 was not found. Install Python 3.10+ and rerun this script."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$StepName
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE"
    }
}

if ($ForceRecreate -and (Test-Path -LiteralPath $VenvDir)) {
    $resolved = Resolve-Path -LiteralPath $VenvDir
    if (-not ([string]$resolved).StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected venv path: $resolved"
    }
    Write-Step "Removing existing local .venv..."
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    Write-Step "Creating local .venv..."
    $base = Resolve-BasePython
    if ($base.Count -eq 2 -and $base[0] -eq "py") {
        & py $base[1] -m venv $VenvDir
    } else {
        & $base[0] -m venv $VenvDir
    }
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Virtual environment was not created: $PythonExe"
}

Write-Step "Upgrading pip tooling..."
Invoke-Checked -FilePath $PythonExe -Arguments @("-m", "pip", "install", "-U", "pip", "setuptools", "wheel") -StepName "pip tooling upgrade"

$installTarget = if ($PdfFull) { ".[pdf-full]" } else { "." }
Write-Step "Installing llmxx-client-thin-capture $installTarget..."
Push-Location -LiteralPath $ProjectRoot
try {
    Invoke-Checked -FilePath $PythonExe -Arguments @("-m", "pip", "install", "-e", $installTarget) -StepName "editable package install"
} finally {
    Pop-Location
}

Write-Step "Verifying imports..."
Invoke-Checked -FilePath $PythonExe -Arguments @("-c", "import PySide6, mss, pynput, httpx, cryptography, PIL, numpy, cv2; import llmxx_client_thin_capture; print('ok')") -StepName "import verification"

Write-Step "Ready: $PythonExe"

