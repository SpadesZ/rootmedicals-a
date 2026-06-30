@echo off
REM File Path: rootmedicals-a/llmxx-server/Stop-LLMXX-Server.cmd
REM Timestamp: 2026-06-15 22:08 +08:00
REM Version: v0.1
REM Description:
REM   Double-click launcher for Stop-LLMXX-Server.ps1.
REM Change Notes:
REM   - v0.1: Added folder-click shutdown entry point for non-terminal demos.
REM Safety Notes:
REM   - Stops only the recorded llmxx-server process or a matching uvicorn
REM     process on the configured local port.
REM Verification Notes:
REM   - Verified by PowerShell syntax parse and port/process checks.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Stop-LLMXX-Server.ps1"
pause
