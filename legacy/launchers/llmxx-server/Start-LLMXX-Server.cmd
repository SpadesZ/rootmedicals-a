@echo off
REM File Path: rootmedicals-a/llmxx-server/Start-LLMXX-Server.cmd
REM Timestamp: 2026-06-15 22:08 +08:00
REM Version: v0.1
REM Description:
REM   Double-click launcher for Start-LLMXX-Server.ps1.
REM Change Notes:
REM   - v0.1: Added folder-click startup entry point for non-terminal demos.
REM Safety Notes:
REM   - Uses PowerShell ExecutionPolicy Bypass only for this local script call.
REM Verification Notes:
REM   - Verified by PowerShell syntax parse and local health check.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Start-LLMXX-Server.ps1"
pause
