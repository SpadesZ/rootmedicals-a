@echo off
rem File Path: rootmedicals-a/llmxx-client/launchers/Start-Client-BossDemo-System.cmd
rem Timestamp: 2026-06-16 14:18 +08:00
rem Version: v0.2
rem Description:
rem   Operator launcher for the deterministic boss-demo client stack.
rem Change Notes:
rem   - v0.1: Delegate to apps/local-ocr/Start-LocalOCR-System.ps1 -BossDemo
rem     after moving all client-side modules under llmxx-client.
rem   - v0.2: Launch PowerShell hidden and detach immediately so the operator
rem     does not keep a console window after HIS startup.
rem Safety Notes:
rem   - Starts only the bundled ClinicalGuard mock HIS and LocalOCR tray client
rem     with explicit demo fixture metadata.

setlocal
set "SCRIPT_DIR=%~dp0"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%..\apps\local-ocr\Start-LocalOCR-System.ps1" -BossDemo
exit /b 0
