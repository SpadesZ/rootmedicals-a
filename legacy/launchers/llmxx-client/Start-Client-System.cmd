@echo off
rem File Path: rootmedicals-a/legacy/launchers/llmxx-client/Start-Client-System.cmd
rem Timestamp: 2026-06-16 14:18 +08:00
rem Version: v0.3
rem Description:
rem   Operator launcher for the consolidated client-side stack.
rem Change Notes:
rem   - v0.1: Delegate to apps/local-ocr/Start-LocalOCR-System.ps1 after
rem     moving all client-side modules under llmxx-client.
rem   - v0.2: Launch PowerShell hidden and detach immediately so the operator
rem     does not keep a console window after HIS startup.
rem   - v0.3: Fix the relative path left over from the move into legacy/launchers,
rem     and fail loudly instead of silently when the target is absent.
rem Safety Notes:
rem   - Starts only the bundled ClinicalGuard mock HIS and LocalOCR tray client.

setlocal
set "SCRIPT_DIR=%~dp0"
rem 這支原本住在 llmxx-client\launchers\，搬到 legacy\launchers\llmxx-client\ 時
rem 相對路徑沒跟著改：`..\apps\` 會解析成 legacy\launchers\apps\（不存在）。
rem 要往上三層才回到 rootmedicals-a 根目錄。
rem 另外 LocalOCR app 已在薄客戶端改版中整個移除，所以先檢查再啟動 —— 少了這道檢查，
rem -WindowStyle Hidden 會讓「檔案不存在」完全靜默，按下去沒反應也沒有任何訊息。
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
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%TARGET%"
exit /b 0
