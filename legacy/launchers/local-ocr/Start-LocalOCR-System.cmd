@echo off
REM File Path: rootmedicals-a/llmxx-client/apps/local-ocr/Start-LocalOCR-System.cmd
REM Timestamp: 2026-06-16 14:18 +08:00
REM Version: v0.3
REM Description:
REM   Double-click launcher for the bundled ClinicalGuard mock HIS and LocalOCR tray.
REM Change Notes:
REM   - v0.1: Added ExecutionPolicy Bypass wrapper so Windows users can start
REM     the rootmedicals-a LocalOCR demo system from File Explorer.
REM   - v0.2: Update file note after consolidating LocalOCR under llmxx-client.
REM   - v0.3: Launch PowerShell hidden and detach immediately so startup does
REM     not leave a console window behind the HIS UI.
REM Safety Notes:
REM   - Delegates to Start-LocalOCR-System.ps1 in the same folder. Does not
REM     change registry execution policy and does not touch external folders.
REM Verification Notes:
REM   - Covered by direct PowerShell Bypass start and process-state validation.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%Start-LocalOCR-System.ps1"
exit /b 0
