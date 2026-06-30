<!--
檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/README.md
產生時間: 2026-06-17 21:35 +08:00
版本: v0.2-薄客戶端整理
說明: thin capture client 使用說明；client 只做截圖、hotkey、醫師浮窗與送出，OCR/RAG 由 llmxx-server 負責。
-->

# llmxx-client thin capture client

這個資料夾是醫師端 thin client，不再是 OCR client。

目前職責很單純：

1. 啟動 ClinicalGuard mock HIS。
2. 監聽 Ctrl+Alt+G 或 tray 手動觸發。
3. 擷取 HIS 視窗截圖，附上 SOAP / vital / ICD 欄位座標提示。
4. 讀取 ClinicalGuard 產生的 ICD sidecar，送出 `icd10_code`、`diagnosis_label`、`normalized_diagnosis`。
5. POST 到 `llmxx-server /api/intake`。
6. polling server session result，顯示醫師端 always-on-top alert。

OCR、SOAP 解析、ICD/A/P 檢查、RAG 查詢、LAVA task 與 final gate 全部在 `llmxx-server` 或 RAG 端執行。

## 啟動方式

一般使用請從根目錄控制台啟動：

```powershell
C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\RootMedicals-Control.cmd
```

內部單獨啟動 client/HIS：

```powershell
cd "C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-client\apps\thin-capture-client"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-ThinCapture-System.ps1
```

停止：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Stop-ThinCapture-System.ps1
```

## 重要檔案

- `src/llmxx_client_thin_capture/pipeline.py`: 截圖、欄位座標提示、ICD sidecar、送 server 的主流程。
- `src/llmxx_client_thin_capture/tray_app.py`: tray、Ctrl+Alt+G、醫師端 alert polling。
- `src/llmxx_client_thin_capture/doctor_alert_widget.py`: 醫師端燈號浮窗。
- `src/llmxx_client_thin_capture/payload.py`: thin screenshot payload 組裝與診斷檔遮蔽。
- `config/default_config.json`: endpoint、hotkey、alert polling 與 capture layout 設定。

## 診斷資料

啟動後會產生：

- `runtime/`: 程序狀態與 stdout/stderr log。
- `diagnostics/`: 已遮蔽截圖的本機診斷 JSON。

診斷檔不應保存完整截圖 base64。若要看 OCR 結果，請看 llmxx-server `/demo/latest` 或 server log。
