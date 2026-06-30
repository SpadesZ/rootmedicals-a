@echo off
REM File Path: rootmedicals-a/legacy/launchers/ebm-rag/Restart-RAG-LAVA-System.cmd
REM Timestamp: 2026-06-17 15:05 +08:00
REM Version: v0.2
REM Description:
REM   Double-click wrapper for restarting and validating the rootmedicals-a
REM   EBM-RAG/LAVA Docker stack.
REM Change Notes:
REM   - v0.1: Delegates to Restart-RAG-LAVA-System.ps1 with ExecutionPolicy Bypass.
REM   - v0.2: Move to legacy launchers after RootMedicals-Control became the public entry.
REM Safety Notes:
REM   - Does not delete Docker volumes or external project folders.
REM Verification Notes:
REM   - Confirms /api/lava/tasks exposes the current 8-task registry.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Restart-RAG-LAVA-System.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Restart-RAG-LAVA-System.ps1 failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
