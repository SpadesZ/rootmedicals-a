# 檔案路徑: rootmedicals-a/Build-ClientDelivery.ps1
# 產生時間: 2026-09-10
# 版本: v0.1
# 說明:
#   從 repo 打出醫師端交付包 RootMedicals-HIS-Client-<日期>.zip。
# 為什麼需要這支:
#   在此之前交付包是手工組的，而三個關鍵檔案不在 repo 裡（README-START-HERE.txt、
#   Start-RootMedicals-HIS-Demo.cmd，以及被 .gitignore 擋掉的 *.local.json），
#   所以「從原始碼重現交付包」做不到，每次都得靠人記得所有步驟。
#   最容易忘的是 config 那步：RootMedicals-Control.ps1 讀的是 default_config.json，
#   但 repo 裡那份是本機開發用的 127.0.0.1:8017，VM 位址只存在於 default_config.vm.json。
#   忘了複製，client 就會靜靜指向 localhost，沒有任何錯誤訊息 —— 2026-09-04 那包
#   就是這條路徑出的事（指向一個已無機器的 IP，實測 timeout）。
# 安全邊界:
#   只讀 repo、只寫輸出目錄與系統 TEMP 的暫存區；不改 repo 內任何檔案
#   （對照舊的 rebuild_vm_delivery.py，它會就地改寫 default_config.vm.json）。
#   不刪除既有交付包，只在發現舊包時列出來提醒。
# 驗證方式:
#   打包後會重新打開 zip 檢查 endpoint、洩漏清單、控制台按鈕與必要檔案，
#   任何一項不過就 exit 1。可先用 -WhatIfOnly 只做檢查不產出檔案。
# ----------------------------------------------------------------------------------------------------

[CmdletBinding()]
param(
    # 後端位址。預設沿用 config/default_config.vm.json 目前登記的值。
    [string]$BackendUrl = "",
    # 輸出目錄。預設放在 repo 的上一層，與既有交付包同一個位置。
    [string]$OutputDir = "",
    # 只跑組裝與檢查，不產生 zip。
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$ClientRoot = Join-Path $RepoRoot "llmxx-client"
$ConfigDir = Join-Path $ClientRoot "apps\thin-capture-client\config"
$VmConfigPath = Join-Path $ConfigDir "default_config.vm.json"

if (-not $OutputDir) { $OutputDir = Split-Path -Parent $RepoRoot }
$Stamp = Get-Date -Format "yyyyMMdd"
$PackageName = "RootMedicals-HIS-Client-$Stamp"
$ZipPath = Join-Path $OutputDir "$PackageName.zip"

function Write-Step { param([string]$Message) Write-Host "[build] $Message" }
function Fail { param([string]$Message) Write-Host "[build] FAIL: $Message" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 1. 決定 endpoint
if (-not (Test-Path -LiteralPath $VmConfigPath)) { Fail "找不到 $VmConfigPath" }
$vmConfig = Get-Content -LiteralPath $VmConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $BackendUrl) { $BackendUrl = [string]$vmConfig.network.endpoint_url }

$uri = $null
if (-not [Uri]::TryCreate($BackendUrl, [UriKind]::Absolute, [ref]$uri)) { Fail "endpoint 不是合法網址: $BackendUrl" }
if ($uri.Scheme -notin @("http", "https")) { Fail "endpoint scheme 必須是 http/https: $BackendUrl" }
# 這道檢查就是這支腳本存在的主要理由：本機位址交付出去等於整包不會動，而且不會報錯。
if ($uri.IsLoopback) { Fail "endpoint 指向本機 ($BackendUrl)。交付包必須指向實際後端，請用 -BackendUrl 指定。" }
$BackendBase = $uri.GetLeftPart([UriPartial]::Authority)
Write-Step "endpoint = $BackendUrl"

# ---------------------------------------------------------------- 2. 組裝暫存區
$Stage = Join-Path ([System.IO.Path]::GetTempPath()) "rootmedicals-delivery-$Stamp-$PID"
$Root = Join-Path $Stage $PackageName
New-Item -ItemType Directory -Force -Path $Root | Out-Null

# 排除規則。tests/ 與 legacy/ 不進交付包；.venv/runtime/diagnostics 可能含本機病歷或
# 環境殘留；*.local.json 是本機開發設定（且被 .gitignore 擋著，本來就不該外流）。
#
# "data" 是這裡最重要的一條，因為它不明顯：clinicalguard-standalone/data/ 全是執行期
# 狀態，其中 current_icd_selection.json 是 mock HIS 與 thin capture 之間的 sidecar，
# 內容是**打包者最後一次輸入的完整 SOAP 文字**（實測含 dx_text 與 S/O/A/P）。
# 那個目錄整包未被 git 追蹤，所以只要在本機跑過一次 demo 就會生出來，
# 手工打包很容易連同病歷一起交付出去。llmxx-client 底下唯一的 data/ 就是它
# （其餘都在已排除的 .venv/ 與 legacy/ 內），shared_data 走另一條複製路徑不受影響。
$ExcludeDirs = @(".venv", "runtime", "diagnostics", "data", "test_exports", "__pycache__",
                 ".pytest_cache", ".mypy_cache", "build", "dist", "tests", "legacy", ".git")
$ExcludeFilePatterns = @("*.pyc", "*.pyo", "*.log", "*.db", "*.local.json", "*.egg-info")

function Copy-Tree {
    param([string]$Source, [string]$Destination)
    $sourceFull = (Resolve-Path -LiteralPath $Source).Path
    foreach ($item in Get-ChildItem -LiteralPath $sourceFull -Recurse -File) {
        $relative = $item.FullName.Substring($sourceFull.Length).TrimStart('\')
        $segments = $relative -split '\\'
        if ($segments[0..($segments.Length - 2)] | Where-Object { $ExcludeDirs -contains $_ }) { continue }
        $skip = $false
        foreach ($pattern in $ExcludeFilePatterns) { if ($item.Name -like $pattern) { $skip = $true; break } }
        if ($skip) { continue }
        $target = Join-Path $Destination $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $item.FullName -Destination $target -Force
    }
}

Write-Step "複製 llmxx-client ..."
Copy-Tree -Source $ClientRoot -Destination (Join-Path $Root "llmxx-client")

Write-Step "複製 shared_data ..."
$icd10Source = Join-Path $RepoRoot "shared_data\icd10\icd10cm_master_demo.json"
if (-not (Test-Path -LiteralPath $icd10Source)) { Fail "找不到 ICD-10 對照表: $icd10Source" }
New-Item -ItemType Directory -Force -Path (Join-Path $Root "shared_data\icd10") | Out-Null
Copy-Item -LiteralPath $icd10Source -Destination (Join-Path $Root "shared_data\icd10\icd10cm_master_demo.json")
# client 執行期會往這三個目錄寫東西，保留空目錄避免首次啟動找不到路徑。
foreach ($dir in @("drafts", "ebm_cohorts", "llmxx_raw_json")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "shared_data\$dir") | Out-Null
}

# ---------------------------------------------------------------- 3. 產生交付用設定
# RootMedicals-Control.ps1 讀的是 default_config.json，不是 .vm.json。這一步是整個
# 打包流程最容易漏掉、漏掉又完全不會報錯的地方，所以固定寫死在這裡。
Write-Step "以 default_config.vm.json 產生 default_config.json ..."
$stagedConfigDir = Join-Path $Root "llmxx-client\apps\thin-capture-client\config"
$deliveryConfig = Get-Content -LiteralPath $VmConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$deliveryConfig.network.endpoint_url = $BackendUrl
$deliveryConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $stagedConfigDir "default_config.json") -Encoding UTF8

# ---------------------------------------------------------------- 4. 產生包層級檔案
Write-Step "產生 README-START-HERE.txt 與 Start-RootMedicals-HIS-Demo.cmd ..."
$readme = @"
RootMedicals HIS Client - GCP Demo
==================================

Backend:
  $BackendBase   (Live RAG)

Requirements:
  - Windows 10 or 11
  - Python 3.10 or newer
  - Internet access during first startup

Start:
  1. Extract the complete ZIP to a SHORT local folder path, for example
     C:\RootMedicals or C:\Users\<you>\Desktop\RootMedicals.
     Do not extract inside a deeply nested folder: once the internal path gets
     long, PySide6 fails to start with
     "ImportError: DLL load failed while importing Shiboken".
  2. Double-click Start-RootMedicals-HIS-Demo.cmd.
  3. The first startup creates a local Python environment and may take several minutes.
  4. Click "Start Live RAG" in the RootMedicals control window.
     This is the only start mode; the backend runs Live RAG.
  5. ClinicalGuard (the mock HIS) opens. You have two ways to send a case:
     - Fastest: use the three buttons in the "Demo 情境" panel at the top right
       (綠燈 / 黃燈 / 橘燈). Each one fills a complete case and submits it for you.
     - Manual: type de-identified SOAP/vitals yourself, then press Ctrl+Alt+G.
  6. The EBM alert window appears beside ClinicalGuard with the traffic light,
     diagnosis, ICD, plan and a short comment. Click it to expand the detail,
     including the literature the judgement was based on.

What the lights mean:
  GREEN   plan may be referenced; no obvious red flag or contraindication found
  YELLOW  physician review needed (for example ICD-10 missing, or evidence
          support below the direct-use threshold)
  ORANGE  high risk; review before proceeding (for example a hard contraindication)
  There is no red light. ORANGE is the most severe clinical state.
  A grey "PENDING" state simply means the result has not come back yet.

Notes:
  - The first submission after the backend restarts is slower (about a minute)
    because the OCR model is loaded on demand. Later ones are faster.
  - The alert window floats on top. Drag it aside if it covers the HIS fields.

Important:
  This demo package uses unencrypted HTTP to the temporary VM endpoint.
  Do not enter real patient names, identifiers, screenshots, or protected health information.
  Replace the endpoint with trusted HTTPS before clinical or production use.
"@
Set-Content -LiteralPath (Join-Path $Root "README-START-HERE.txt") -Value $readme -Encoding UTF8

$launcher = @"
@echo off
cd /d "%~dp0llmxx-client"
call RootMedicals-Control.cmd
"@
Set-Content -LiteralPath (Join-Path $Root "Start-RootMedicals-HIS-Demo.cmd") -Value $launcher -Encoding UTF8

# ---------------------------------------------------------------- 5. 打包前自我檢查
Write-Step "檢查組裝結果 ..."
$problems = @()

$requiredFiles = @(
    "README-START-HERE.txt",
    "Start-RootMedicals-HIS-Demo.cmd",
    "llmxx-client\RootMedicals-Control.cmd",
    "llmxx-client\RootMedicals-Control.ps1",
    "llmxx-client\apps\thin-capture-client\config\default_config.json",
    "llmxx-client\apps\thin-capture-client\Setup-ThinCapture-Environment.ps1",
    "llmxx-client\apps\thin-capture-client\src\llmxx_client_thin_capture\main.py",
    "llmxx-client\apps\clinicalguard-standalone\src\clinical_guard_app\gui_app.py",
    "shared_data\icd10\icd10cm_master_demo.json"
)
foreach ($relative in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $relative))) { $problems += "缺少必要檔案: $relative" }
}

$stagedEndpoint = [string]((Get-Content -LiteralPath (Join-Path $stagedConfigDir "default_config.json") -Raw -Encoding UTF8 | ConvertFrom-Json).network.endpoint_url)
if ($stagedEndpoint -ne $BackendUrl) { $problems += "default_config.json 的 endpoint 是 $stagedEndpoint，應為 $BackendUrl" }

# 控制台只該有一個啟動入口。LiveSynthetic / DemoFixture 是 server 端互斥狀態，
# client 切不了，放在畫面上按了必定失敗（見 RootMedicals-Control.ps1 的維護筆記）。
$controlText = Get-Content -LiteralPath (Join-Path $Root "llmxx-client\RootMedicals-Control.ps1") -Raw -Encoding UTF8
if ($controlText -match '\$btnDemo|\$btnFixture') { $problems += "控制台仍有 LiveSynthetic/DemoFixture 按鈕，這兩個模式在 VM 部署下必定失敗" }
if ($controlText -notmatch '\$btnLive\.Text') { $problems += "控制台缺少 Start Live RAG 按鈕" }

# 病歷外洩是這裡唯一不可逆的錯誤：zip 一旦寄出去就收不回來，所以獨立再查一次，
# 不倚賴上面的排除規則有沒有寫對。
$phiLike = Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
    Where-Object { $_.Name -in @("current_icd_selection.json") -or $_.Extension -in @(".db", ".sqlite", ".sqlite3") }
if ($phiLike) {
    $problems += "包含執行期病歷/資料庫檔案: " + (($phiLike | ForEach-Object { $_.Name }) -join ", ")
}

$leaked = Get-ChildItem -LiteralPath $Root -Recurse -Force | Where-Object {
    $path = $_.FullName
    ($ExcludeDirs | Where-Object { $path -match [regex]::Escape("\$_\") }) -or
    ($_.PSIsContainer -eq $false -and ($ExcludeFilePatterns | Where-Object { $_ -ne "*.egg-info" -and $path -like "*$_" }))
}
if ($leaked) { $problems += "包含應排除的內容: " + (($leaked | Select-Object -First 5 | ForEach-Object { $_.Name }) -join ", ") }

if ($problems.Count -gt 0) {
    Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($problem in $problems) { Write-Host "  - $problem" -ForegroundColor Red }
    Fail "自我檢查未通過，未產生 zip。"
}

$fileCount = (Get-ChildItem -LiteralPath $Root -Recurse -File).Count
Write-Step "組裝完成: $fileCount 個檔案"

if ($WhatIfOnly) {
    Write-Step "-WhatIfOnly：僅檢查，未產生 zip。暫存區 $Root"
    exit 0
}

# ---------------------------------------------------------------- 6. 壓縮並回頭驗證 zip
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
Compress-Archive -Path $Root -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue

# 檢查真正產出的 zip，而不是只信暫存區 —— 壓縮本身也可能漏檔。
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
    $entryNames = $zip.Entries | ForEach-Object { $_.FullName }
    $configEntry = $zip.Entries | Where-Object { $_.FullName -like "*/thin-capture-client/config/default_config.json" }
    if (-not $configEntry) { Fail "zip 內找不到 default_config.json" }
    $reader = New-Object System.IO.StreamReader($configEntry.Open())
    $zipEndpoint = [string]((($reader.ReadToEnd()) | ConvertFrom-Json).network.endpoint_url)
    $reader.Close()
    if ($zipEndpoint -ne $BackendUrl) { Fail "zip 內 endpoint 是 $zipEndpoint，應為 $BackendUrl" }
    $zipLeak = $entryNames | Where-Object {
        $_ -match '/(\.venv|runtime|diagnostics|data|test_exports|__pycache__|legacy|tests)/' -or
        $_ -like "*.local.json" -or $_ -like "*.db" -or $_ -like "*current_icd_selection.json"
    }
    if ($zipLeak) { Fail ("zip 內含應排除的內容: " + ($zipLeak -join ", ")) }
    $zipFileCount = ($zip.Entries | Where-Object { -not $_.FullName.EndsWith("/") }).Count
} finally {
    $zip.Dispose()
}

Write-Host ""
Write-Step "完成 -> $ZipPath"
Write-Step ("大小 {0:N0} KB / {1} 個檔案 / endpoint {2}" -f ((Get-Item $ZipPath).Length / 1KB), $zipFileCount, $zipEndpoint)

$older = Get-ChildItem -LiteralPath $OutputDir -Filter "RootMedicals-HIS-Client-*.zip" |
    Where-Object { $_.FullName -ne $ZipPath }
if ($older) {
    Write-Host ""
    Write-Host "[build] 注意：輸出目錄還有其他交付包，交錯版本容易發錯。確認後再自行刪除：" -ForegroundColor Yellow
    $older | ForEach-Object { Write-Host "  $($_.Name)  ($($_.LastWriteTime))" -ForegroundColor Yellow }
}
