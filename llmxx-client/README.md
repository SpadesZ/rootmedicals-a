<!--
  檔案路徑: rootmedicals-a/llmxx-client/README.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: RootMedicals-A 交付文字檔，提供系統設定、文件或輔助程式。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

# File Path: rootmedicals-a/llmxx-client/README.md
# Timestamp: 2026-06-17 15:05 +08:00
# Version: v0.2
#
# Description:
#   Client-side module index for the rootmedicals-a delivery package.
#
# Change Notes:
#   - v0.1: Define the consolidated client tree after moving ClinicalGuard,
#     LocalOCR, and legacy capture code under llmxx-client.
#   - v0.2: Retire module-level CMD launchers from the public surface; the
#     root RootMedicals-Control panel is now the only operator entry point.
#
# Safety Notes:
#   - This document is informational only and has no runtime side effects.
# ----------------------------------------------------------------------------------------------------

# llmxx-client

This folder is the strict client-side boundary for the deliverable `rootmedicals-a` system.

## Structure

- `apps/local-ocr` - active LocalOCR tray client, hotkey capture, DoctorAlertWidget, payload writer, and client launch scripts.
- `apps/clinicalguard-standalone` - bundled ClinicalGuard mock HIS used for local end-to-end demos.
- `legacy/qt-capture` - earlier Qt capture prototype kept for reference only.

## Entry Points

For operators and demos, start from the project root:

```powershell
cd "<rootmedicals-a>"
.\RootMedicals-Control.cmd
```

The control panel delegates to internal PowerShell helpers under
`apps/local-ocr`. Legacy double-click CMD wrappers were moved to
`../legacy/launchers` to reduce accidental entry points.

Server-side entry points remain under `../llmxx-server`.
