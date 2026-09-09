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
# Version: v0.3
#
# Description:
#   Client-side module index for the rootmedicals-a delivery package.
#
# Change Notes:
#   - v0.1: Define the consolidated client tree after moving ClinicalGuard,
#     LocalOCR, and legacy capture code under llmxx-client.
#   - v0.2: Retire module-level CMD launchers from the public surface; the
#     root RootMedicals-Control panel is now the only operator entry point.
#   - v0.3: LocalOCR was replaced by the thin capture client; `apps/local-ocr`
#     no longer exists. Record the client control panel that ships in the
#     delivery ZIP alongside the root one.
#
# Safety Notes:
#   - This document is informational only and has no runtime side effects.
# ----------------------------------------------------------------------------------------------------

# llmxx-client

This folder is the strict client-side boundary for the deliverable `rootmedicals-a` system.

## Structure

- `apps/thin-capture-client` - active client. It captures the HIS window, sends the
  screenshot to `llmxx-server`, and renders the result in the DoctorAlertWidget. It
  owns the Ctrl+Alt+G hotkey, the tray app and the client launch scripts.
  It deliberately does **no** OCR and no medical normalization: both run on
  `llmxx-server`, so the client stays thin.
- `apps/clinicalguard-standalone` - bundled ClinicalGuard mock HIS used for local
  end-to-end demos. It also writes the ICD sidecar
  (`data/current_icd_selection.json`) that the thin capture client reads.
- `legacy/qt-capture` - earlier Qt capture prototype kept for reference only.

The former `apps/local-ocr` tray client was removed when the client was thinned
out; what remains of it is `../legacy/client-local-ocr-legacy` (library modules,
reference only) and `../legacy/launchers` (dead double-click wrappers). Neither is
on the supported path.

## Entry Points

Two control panels exist; pick by what you are running.

Full local stack (server + client + RAG/LAVA), from the project root:

```powershell
cd "<rootmedicals-a>"
.\RootMedicals-Control.cmd
```

Client only, pointed at an already-deployed `llmxx-server` (this is the one that
ships in the `RootMedicals-HIS-Client-*.zip` delivery package):

```powershell
cd "<rootmedicals-a>\llmxx-client"
.\RootMedicals-Control.cmd
```

The client panel creates `apps/thin-capture-client/.venv` on first run, then
delegates to `RootMedicals-Control.ps1`. That script reads the backend address
from `apps/thin-capture-client/config/default_config.json` — note it reads
`default_config.json`, not the `.vm.json` / `.local.json` templates, so a delivery
package must have the intended template copied over `default_config.json`.

Server-side entry points remain under `../llmxx-server`.
