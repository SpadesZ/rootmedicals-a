<!--
檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/HANDOFF.md
產生時間: 2026-06-17 21:35 +08:00
版本: v0.2-薄客戶端整理
說明: thin capture client 交接重點；舊 client-side OCR 已移出 active app。
-->

# thin capture client 交接

## 現在的邊界

client 端不做 OCR。client 端只做：HIS 視窗定位、截圖、layout hints、ICD sidecar 讀取、POST、alert polling、醫師端浮窗。

server 端負責：截圖解碼、裁切、OCR、normalization、formal payload、ICD/A/P gate、RAG、LAVA、final gate。

## 閉環流程

```text
ClinicalGuard mock HIS
-> Ctrl+Alt+G / tray
-> thin capture client 截圖 + layout hints + ICD sidecar
-> POST http://127.0.0.1:8017/api/intake
-> llmxx-server server-side OCR
-> clinical mapper + ICD/A/P gate
-> RAG / LAVA task / demo fixture 規則
-> final gate
-> client polling session result
-> DoctorAlertWidget 顯示綠/黃/橘燈
```

## 啟停

首選：

```powershell
C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\RootMedicals-Control.cmd
```

內部腳本：

```powershell
.\Start-ThinCapture-System.ps1
.\Stop-ThinCapture-System.ps1
.\Setup-ThinCapture-Environment.ps1
```

## payload contract

client 送出的是：

- `schema_version = llmxx-client-screenshot.v0.1`
- `screenshot.image_b64`
- `layout_regions[]`
- `clinical_metadata.icd10_code`
- `clinical_metadata.diagnosis_label`
- `clinical_metadata.normalized_diagnosis`
- `demo_mode/demo_fixture_id`，僅在 demo fixture 模式開啟時存在

本機 diagnostics 會遮蔽 `screenshot.image_b64`，避免落地保存病歷畫面。

## legacy

舊 client-side OCR 模組已移到：

```text
rootmedicals-a/legacy/client-local-ocr-legacy
```

active client 不應 import 這些 legacy OCR 檔案。若未來要恢復 client OCR，必須另開架構討論，不能直接塞回 thin capture client。
