REM 檔案路徑: rootmedicals-a/RootMedicals-Control.cmd
REM 產生時間: 2026-06-17 16:10 +08:00
REM 版本: v0.1-交付整理
REM 說明: RootMedicals-A 根目錄交付入口或總覽設定。
REM 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
REM ----------------------------------------------------------------------------------------------------

@echo off
REM File Path: rootmedicals-a/RootMedicals-Control.cmd
REM Timestamp: 2026-06-16 20:40 +08:00
REM Version: v0.1
REM Description:
REM   Double-click entry for the RootMedicals Live RAG / Demo Fixture control panel.
REM Change Notes:
REM   - v0.1: Launch RootMedicals-Control.ps1 in a hidden PowerShell host.
REM Safety Notes:
REM   - Delegates all work to RootMedicals-Control.ps1 inside this project.
REM Verification Notes:
REM   - Validate by double-clicking this file or running RootMedicals-Control.ps1 -Action Status.
REM ----------------------------------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%RootMedicals-Control.ps1"
exit /b 0
