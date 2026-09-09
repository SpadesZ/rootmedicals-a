@echo off
rem File Path: rootmedicals-a/legacy/launchers/llmxx-client/Stop-Client-System.cmd
rem Timestamp: 2026-06-16 14:18 +08:00
rem Version: v0.3
rem Description:
rem   Operator launcher that stops the consolidated client-side stack.
rem Change Notes:
rem   - v0.1: Delegate to apps/local-ocr/Stop-LocalOCR-System.ps1 after
rem     moving all client-side modules under llmxx-client.
rem   - v0.2: Launch PowerShell hidden and detach immediately so shutdown does
rem     not keep a console window open.
rem   - v0.3: Fix the relative path left over from the move into legacy/launchers,
rem     and fail loudly instead of silently when the target is absent.
rem Safety Notes:
rem   - Stops only processes tracked by the LocalOCR runtime state file.

setlocal
set "SCRIPT_DIR=%~dp0"
rem 與同資料夾的 Start-*.cmd 同一個相對路徑問題：搬到 legacy\launchers\llmxx-client\
rem 之後要往上三層才回到 rootmedicals-a 根目錄。
rem LocalOCR app 已隨薄客戶端改版移除，因此先檢查再啟動。
set "TARGET=%SCRIPT_DIR%..\..\..\llmxx-client\apps\local-ocr\Stop-LocalOCR-System.ps1"
if not exist "%TARGET%" (
    echo [legacy launcher] Target not found:
    echo   %TARGET%
    echo.
    echo The LocalOCR client was replaced by llmxx-client\apps\thin-capture-client.
    echo Stop demos from rootmedicals-a\RootMedicals-Control.cmd instead.
    pause
    exit /b 1
)
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%TARGET%"
exit /b 0
