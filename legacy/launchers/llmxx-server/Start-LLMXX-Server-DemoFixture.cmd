@echo off
REM File Path: rootmedicals-a/llmxx-server/Start-LLMXX-Server-DemoFixture.cmd
REM Timestamp: 2026-06-16 17:35 +08:00
REM Version: v0.1
REM Description:
REM   Double-click launcher for llmxx-server with explicit deterministic demo fixture mode.
REM Change Notes:
REM   - v0.1: Starts Start-LLMXX-Server.ps1 with -DemoFixture for deterministic
REM     evidence fixture demonstrations.
REM Safety Notes:
REM   - Enables only the local process environment for this server start. It
REM     does not modify config files, API keys, or production RAG settings.
REM Verification Notes:
REM   - Validate with GET http://127.0.0.1:8017/api/health checks.demo_fixture.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Start-LLMXX-Server.ps1" -DemoFixture
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Start-LLMXX-Server.ps1 -DemoFixture failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
