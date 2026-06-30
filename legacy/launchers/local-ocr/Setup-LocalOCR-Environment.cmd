@echo off
REM File Path: rootmedicals-a/llmxx-client-local-ocr/Setup-LocalOCR-Environment.cmd
REM Timestamp: 2026-06-15 22:34 +08:00
REM Version: v0.1
REM Description:
REM   Double-click setup wrapper for creating or repairing the LocalOCR Python
REM   virtual environment inside rootmedicals-a.
REM Change Notes:
REM   - v0.1: Added ExecutionPolicy Bypass wrapper for first-time demo setup.
REM Safety Notes:
REM   - Delegates to Setup-LocalOCR-Environment.ps1 in the same folder. The
REM     PowerShell script constrains environment writes to this project folder.
REM Verification Notes:
REM   - Covered by successful setup/import verification in the local .venv.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Setup-LocalOCR-Environment.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo LocalOCR environment setup completed.
) else (
  echo Setup-LocalOCR-Environment.ps1 failed with exit code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
