@echo off
title RootMedicals Control

set "VENV_DIR=%~dp0apps\thin-capture-client\.venv"

if not exist "%VENV_DIR%" (
    echo [RootMedicals] First-time startup detected. Setting up local Python environment...
    echo [RootMedicals] This may take a minute. Please keep internet connected...
    echo.
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apps\thin-capture-client\Setup-ThinCapture-Environment.ps1"
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Environment setup failed! Please make sure Python 3.10+ is installed on this PC.
        pause
        exit /b %errorlevel%
    )
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RootMedicals-Control.ps1" %*
