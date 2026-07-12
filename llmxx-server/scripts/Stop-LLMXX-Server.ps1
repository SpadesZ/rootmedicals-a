# 檔案路徑: rootmedicals-a/llmxx-server/scripts/Stop-LLMXX-Server.ps1
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmxx-server 內部維運腳本，供 RootMedicals-Control 或工程診斷呼叫。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

param(
    [int]$Port = 8017
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $PSCommandPath
$ServerDir = Split-Path -Parent $ScriptDir
$DataDir = Join-Path $ServerDir "data"
$PidPath = Join-Path $DataDir "llmxx_server_$Port.pid"

function Write-Step {
    param([string]$Message)
    Write-Host "[LLMXX] $Message"
}

function Stop-ProcessIfRunning {
    param([int]$ProcessId)
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    Stop-Process -Id $ProcessId -Force
    return $true
}

Write-Step "Stopping llmxx-server on port $Port..."

$stopped = $false
if (Test-Path -LiteralPath $PidPath) {
    $pidText = (Get-Content -LiteralPath $PidPath -Raw).Trim()
    $pidValue = 0
    if ([int]::TryParse($pidText, [ref]$pidValue)) {
        if (Stop-ProcessIfRunning -ProcessId $pidValue) {
            Write-Step "Stopped recorded PID $pidValue."
            $stopped = $true
        }
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $connections) {
    $owner = [int]$conn.OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue
    $commandLine = [string]($proc.CommandLine)
    # 維護筆記：2026-06 內層 Python package 從 llmxx_server 改名為 server_app。
    # Stop 腳本同時認得舊/新 module path，避免使用者正在跑舊行程時停不掉。
    $safeEntry = $commandLine -like "*main:app*" -or $commandLine -like "*server_app.main:app*" -or $commandLine -like "*llmxx_server.main:app*"
    $safeMatch = $commandLine -like "*uvicorn*" -and $safeEntry -and $commandLine -like "*--port*" -and $commandLine -like "*$Port*"
    if ($safeMatch -and (Stop-ProcessIfRunning -ProcessId $owner)) {
        Write-Step "Stopped matching uvicorn PID $owner."
        $stopped = $true
    }
}

Start-Sleep -Milliseconds 500
$remaining = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue

# 保底：如果命令列比對沒抓到（例如上一輪啟動洩漏的殘留行程、command line 讀不到），
# 但佔用 $Port 的行程本身就是 python/pythonw，仍視為我們的 server 殘留並收掉，
# 讓下一次啟動不會因為埠口被舊行程卡住而失敗。只針對本埠口的 python 映像，不動其他服務。
foreach ($conn in $remaining) {
    $owner = [int]$conn.OwningProcess
    $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
    if ($null -ne $proc -and $proc.ProcessName -in @("python", "pythonw")) {
        if (Stop-ProcessIfRunning -ProcessId $owner) {
            Write-Step "Stopped leaked python holding port $Port (PID $owner)."
            $stopped = $true
        }
    }
}

Start-Sleep -Milliseconds 300
$remaining = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Host "[LLMXX] Port $Port is still listening, but no safe llmxx-server process was stopped." -ForegroundColor Yellow
    Write-Host "[LLMXX] I left it alone because it may belong to another service." -ForegroundColor Yellow
    exit 1
}

if ($stopped) {
    Write-Step "Stopped."
} else {
    Write-Step "No running llmxx-server process found."
}

