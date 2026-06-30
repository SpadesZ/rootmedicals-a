@echo off
rem File Path: rootmedicals-a/llmxx-client/launchers/Stop-Client-System.cmd
rem Timestamp: 2026-06-16 14:18 +08:00
rem Version: v0.2
rem Description:
rem   Operator launcher that stops the consolidated client-side stack.
rem Change Notes:
rem   - v0.1: Delegate to apps/local-ocr/Stop-LocalOCR-System.ps1 after
rem     moving all client-side modules under llmxx-client.
rem   - v0.2: Launch PowerShell hidden and detach immediately so shutdown does
rem     not keep a console window open.
rem Safety Notes:
rem   - Stops only processes tracked by the LocalOCR runtime state file.

setlocal
set "SCRIPT_DIR=%~dp0"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%..\apps\local-ocr\Stop-LocalOCR-System.ps1"
exit /b 0
