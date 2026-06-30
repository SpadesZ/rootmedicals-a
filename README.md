<!--
  檔案路徑: rootmedicals-a/README.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: RootMedicals-A 根目錄交付入口或總覽設定。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

# RootMedicals-A

RootMedicals-A is the deliverable local demo package for the clinical
client-to-RAG EBM closed loop.

## Public Entry Point

Use only the root control panel for demo operation:

```powershell
.\RootMedicals-Control.cmd
```

The control panel starts/stops the local `llmxx-server`, ClinicalGuard mock HIS,
LocalOCR tray client, and RAG/LAVA stack modes. Module-level PowerShell scripts
are kept as internal helpers for the control panel and engineering diagnostics.

## Main Runtime Folders

- `llmxx-client` - ClinicalGuard mock HIS, thin LocalOCR screenshot sender,
  doctor-side alert widget, and client runtime code.
- `llmxx-server` - intake API, server-side OCR/parse bridge, ICD/EBM gate, and
  demo viewer on port `8017`.
- `ebm-rag` - RAG/LAVA execution core, vector retrieval, evidence generation,
  and LAVA task bindings.
- `llmebm` - long-term EBM knowledge/taxonomy UI and local SQLite knowledge
  data.
- `runtime_reports` - verification summaries and handoff artifacts.
- `legacy/launchers` - old double-click CMD wrappers kept only for traceability;
  new operation should use `RootMedicals-Control.cmd`.
