# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/build.ps1
# 產生時間: 2026-06-18 10:42 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard 的 Windows 打包腳本。交付 demo 時通常由 RootMedicals-Control 啟動原始碼版；
#   這支腳本則提供需要獨立 exe 時的 PyInstaller 建置路徑。
# 主要責任:
#   1. 找到專案 venv 或指定 Python。
#   2. 收集 Tkinter/Tcl/Tk runtime 檔案。
#   3. 產出 folder 或 onefile 版本的 ClinicalGuard 可執行檔。
# 維護提醒:
#   - 不要在這裡寫入病患資料或 server endpoint；這支腳本只負責建置。
#   - 若 Python 版本升級，請重新確認 tk86t.dll/tcl86t.dll 與 tcl8.6/tk8.6 路徑。
#   - 建置前會清除該 mode 的 dist/build 目錄，請勿把手動檔案放在那些資料夾裡。
# 驗證方式:
#   - .\build.ps1 -Mode folder
#   - dist\folder\ClinicalGuard\ClinicalGuard.exe 可以開啟醫師端視窗。
# ----------------------------------------------------------------------------------------------------

param(
    [ValidateSet("folder", "onefile")]
    [string]$Mode = "folder",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $appRoot "..\..")).Path

if (-not $PythonPath) {
    # 預設使用 rootmedicals-a 內的 venv；若交付機器路徑不同，可用 -PythonPath 明確指定。
    $PythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $PythonPath)) {
    throw "Python executable not found: $PythonPath"
}

$entry = Join-Path $appRoot "src\clinical_guard_app\gui_app.py"
$srcPath = Join-Path $appRoot "src"
$hooksPath = Join-Path $appRoot "pyinstaller_hooks"
$tkRuntimeHook = Join-Path $hooksPath "rthook_set_tk_paths.py"
$distRoot = Join-Path $appRoot "dist"
$buildRoot = Join-Path $appRoot "build"
$distPath = Join-Path $distRoot $Mode
$workPath = Join-Path $buildRoot $Mode

New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

if (Test-Path $distPath) {
    Remove-Item -LiteralPath $distPath -Recurse -Force
}
if (Test-Path $workPath) {
    Remove-Item -LiteralPath $workPath -Recurse -Force
}

# 解析 base CPython 路徑，因為 tkinter 的 Tcl/Tk runtime 不一定在 venv 內。
# 這是 Windows 打包最容易漏掉的部分；少了這些資料，exe 可以產出但啟動會失敗。
$basePrefix = (& $PythonPath -c "import sys; print(sys.base_prefix)").Trim()
$tclRoot = Join-Path $basePrefix "tcl"
$tclDataDir = Join-Path $tclRoot "tcl8.6"
$tkDataDir = Join-Path $tclRoot "tk8.6"
$tclModuleDir = Join-Path $tclRoot "tcl8"
$tclLibDir = Join-Path $basePrefix "Lib\tkinter"
$tkDll = Join-Path $basePrefix "DLLs\tk86t.dll"
$tclDll = Join-Path $basePrefix "DLLs\tcl86t.dll"

Write-Host "Python base prefix: $basePrefix"
Write-Host "tkinter lib dir: $tclLibDir"
Write-Host "tcl root dir: $tclRoot"
Write-Host "tcl data dir: $tclDataDir"
Write-Host "tk data dir: $tkDataDir"
Write-Host "hooks dir: $hooksPath"

$args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--name", "ClinicalGuard",
    "--paths", $srcPath,
    "--windowed",
    "--distpath", $distPath,
    "--workpath", $workPath,
    "--specpath", $appRoot,
    "--additional-hooks-dir", $hooksPath
)

# Force-include tkinter modules to avoid accidental exclusion in frozen builds.
$args += @(
    "--hidden-import", "tkinter",
    "--hidden-import", "_tkinter",
    "--hidden-import", "tkinter.ttk",
    "--hidden-import", "tkinter.messagebox",
    "--collect-submodules", "tkinter"
)

if (Test-Path $tkRuntimeHook) {
    # runtime hook 會在 GUI 程式碼前設定 TCL_LIBRARY/TK_LIBRARY，避免 frozen app 找不到 init.tcl。
    $args += @("--runtime-hook", $tkRuntimeHook)
}

# Include Tcl/Tk runtime files for packaged GUI startup.
if (Test-Path $tclDataDir) {
    # Match PyInstaller runtime hook expectation (pyi_rth__tkinter): _tcl_data
    $args += @("--add-data", "$tclDataDir;_tcl_data")
}
if (Test-Path $tkDataDir) {
    # Match PyInstaller runtime hook expectation (pyi_rth__tkinter): _tk_data
    $args += @("--add-data", "$tkDataDir;_tk_data")
}
if (Test-Path $tclModuleDir) {
    # Optional Tcl module dir used by some scripts.
    $args += @("--add-data", "$tclModuleDir;tcl8")
}
if (Test-Path $tclLibDir) {
    $args += @("--add-data", "$tclLibDir;tkinter")
}
if (Test-Path $tkDll) {
    $args += @("--add-binary", "$tkDll;.")
}
if (Test-Path $tclDll) {
    $args += @("--add-binary", "$tclDll;.")
}

if ($Mode -eq "onefile") {
    $args += "--onefile"
} else {
    $args += "--onedir"
}

$args += $entry

Write-Host "Building ClinicalGuard ($Mode) ..."
& $PythonPath @args

Write-Host "Build finished."
Write-Host "Output: $distPath"
