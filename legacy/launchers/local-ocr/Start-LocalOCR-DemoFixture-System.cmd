@echo off
REM File Path: rootmedicals-a/llmxx-client/apps/local-ocr/Start-LocalOCR-DemoFixture-System.cmd
REM Timestamp: 2026-06-16 17:35 +08:00
REM Version: v0.1
REM Description:
REM   Double-click launcher for ClinicalGuard plus LocalOCR tray with explicit
REM   deterministic demo fixture payload markers.
REM Change Notes:
REM   - v0.1: Delegates to Start-LocalOCR-System.ps1 -DemoFixture.
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
