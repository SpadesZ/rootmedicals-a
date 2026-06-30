@echo off
REM File Path: rootmedicals-a/llmxx-client/apps/local-ocr/Start-LocalOCR-BossDemo-System.cmd
REM Timestamp: 2026-06-16 14:18 +08:00
REM Version: v0.3
REM Description:
REM   Legacy compatibility launcher. Prefer Start-LocalOCR-DemoFixture-System.cmd.
REM Change Notes:
REM   - v0.1: Delegates to Start-LocalOCR-System.ps1 -BossDemo.
REM   - v0.2: Update file note after consolidating LocalOCR under llmxx-client.
REM   - v0.3: Launch PowerShell hidden and detach immediately so startup does
REM     not leave a console window behind the HIS UI.
REM Safety Notes:
REM   - Enables only the launched tray process environment. It does not alter
REM     config files, OCR models, or patient data.
REM Verification Notes:
REM   - Validate by checking server_payload JSON contains demo_mode and
REM     demo_fixture_id when this launcher is used.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%Start-LocalOCR-System.ps1" -DemoFixture
exit /b 0
