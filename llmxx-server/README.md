<!--
  檔案路徑: rootmedicals-a/llmxx-server/README.md
  產生時間: 2026-06-18 12:20 +08:00
  版本: v0.2
  說明:
    llmxx-server 交接說明。外層 llmxx-server 是可獨立啟動的服務資料夾，
    內層 server_app 是 Python application package。
  維護提醒:
    啟動請優先使用 rootmedicals-a 根目錄的 RootMedicals-Control.cmd。
  ----------------------------------------------------------------------------------------------------
-->

# llmxx-server

這個資料夾負責本機 `8017` intake API、server-side 截圖 OCR 正規化、ICD 解析、
RAG/LAVA adapter 呼叫、最終燈號回應，以及 `/demo/latest` 工程展示頁。

## 對外啟動方式

一般 demo 不直接進這個資料夾啟動，而是從 `rootmedicals-a` 根目錄開控制台：

```powershell
cd "<rootmedicals-a>"
.\RootMedicals-Control.cmd
```

根目錄控制台會再呼叫 `scripts/` 底下的內部啟停腳本。

## 目錄職責

- `server_app/main.py`: `uvicorn server_app.main:app` 的相容入口。
- `server_app/api/`: FastAPI route 層，處理 health、intake、session lookup 與 demo viewer。
- `server_app/core/`: clinical mapping、deterministic adjudication、demo fixture 與 final gate response。
- `server_app/ocr/`: server-side 截圖 OCR 與正式 clinical payload 轉換。
- `server_app/integrations/`: RAG 與 LAVA HTTP adapter。
- `server_app/domain/`: ICD-10 正規化與疾病 taxonomy helper。
- `server_app/infra/`: runtime settings、SQLite state、安全處理與錯誤契約。
- `server_app/contracts/`: server 各層共用的 Pydantic schema。
- `scripts/`: server 內部啟停、replay 與 smoke test 輔助腳本。
- `static/`: demo viewer 的 HTML/CSS/JS。
- `data/`: 本機 SQLite 狀態、log 與 PID 檔。
- `Dockerfile`: container 啟動入口。
- `requirements.txt`: Python runtime 相依套件。

舊的扁平 Python 檔已整理進 `server_app/` package。這樣外層 `llmxx-server`
代表服務專案，內層 `server_app` 代表真正的 Python app，避免同名巢狀目錄讓接手者混淆。
