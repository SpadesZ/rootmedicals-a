@echo off
REM File Path: rootmedicals-a/llmxx-client/apps/local-ocr/Stop-LocalOCR-System.cmd
REM Timestamp: 2026-06-16 14:18 +08:00
REM Version: v0.3
REM Description:
REM   Double-click shutdown wrapper for the bundled ClinicalGuard mock HIS and
REM   LocalOCR tray demo system.
REM Change Notes:
REM   - v0.1: Added ExecutionPolicy Bypass wrapper so Windows users can stop
REM     the rootmedicals-a LocalOCR demo system from File Explorer.
REM   - v0.2: Update file note after consolidating LocalOCR under llmxx-client.
REM   - v0.3: Launch PowerShell hidden and detach immediately so shutdown does
REM     not leave a console window behind the HIS UI.
REM Safety Notes:
REM   - Delegates to Stop-LocalOCR-System.ps1 in the same folder. It only stops
REM     matching LocalOCR/ClinicalGuard demo processes.
REM Verification Notes:
REM   - Covered by direct PowerShell Bypass stop and process-state cleanup.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%Stop-LocalOCR-System.ps1"
exit /b 0
