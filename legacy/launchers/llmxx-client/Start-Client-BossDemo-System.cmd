@echo off
rem File Path: rootmedicals-a/legacy/launchers/llmxx-client/Start-Client-BossDemo-System.cmd
rem Timestamp: 2026-06-16 14:18 +08:00
rem Version: v0.3
rem Description:
rem   Operator launcher for the deterministic boss-demo client stack.
rem Change Notes:
rem   - v0.1: Delegate to apps/local-ocr/Start-LocalOCR-System.ps1 -BossDemo
rem     after moving all client-side modules under llmxx-client.
rem   - v0.2: Launch PowerShell hidden and detach immediately so the operator
rem     does not keep a console window after HIS startup.
rem   - v0.3: Fix the relative path left over from the move into legacy/launchers,
rem     and fail loudly instead of silently when the target is absent.
rem Safety Notes:
rem   - Starts only the bundled ClinicalGuard mock HIS and LocalOCR tray client
rem     with explicit demo fixture metadata.

setlocal
set "SCRIPT_DIR=%~dp0"
rem 相對路徑與 Start-Client-System.cmd 同一個問題：搬到 legacy\launchers\llmxx-client\
rem 之後要往上三層才回到 rootmedicals-a 根目錄，`..\apps\` 會落在 legacy\launchers\apps\。
rem LocalOCR app 已隨薄客戶端改版移除，因此先檢查再啟動。
set "TARGET=%SCRIPT_DIR%..\..\..\llmxx-client\apps\local-ocr\Start-LocalOCR-System.ps1"
if not exist "%TARGET%" (
    echo [legacy launcher] Target not found:
    echo   %TARGET%
    echo.
    echo The LocalOCR client was replaced by llmxx-client\apps\thin-capture-client.
    echo Start demos from rootmedicals-a\RootMedicals-Control.cmd instead.
    pause
    exit /b 1
)
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%TARGET%" -BossDemo
exit /b 0
