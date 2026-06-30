<!--
  檔案路徑: rootmedicals-a/doc/LLMXX_LAVA_RAG_MODIFICATION_PLAN_2026-06-15.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: 交付文件與規劃/驗證紀錄。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

# LLMXX Client -> Server -> RAG/LAVA 修改規畫書

日期：2026-06-15  
範圍：`rootmedicals-a/llmxx-client-local-ocr`、`rootmedicals-a/llmxx-server`、`rootmedicals-a/ebm-rag`、LAVA task binding

## 1. 結論先講

這次修改的核心不是重做 RAG，而是把已經可運作的本機 OCR client 正式接進一條穩定線路：

```text
ClinicalGuard HIS
  -> rootmedicals-a/llmxx-client-local-ocr 產出 server_payload JSON
  -> llmxx-server /api/intake 接收、最小化保存、轉成臨床查詢
  -> ebm-rag /api/v1/rag/check 查證
  -> LAVA 綁定的 LLM 生成 EBM 結果
  -> llmxx-server 加上語意裁判分數與 deterministic hard gate，只能加嚴不可放寬
  -> 回傳 light_color / score / comment / evidence
```

最適合擔任本文件審查員的本機代理角色是：

1. `Clinical EBM Demo Verifier`：最適合主審，因為它專門審 RootMedicals RAG、citation、claim-to-evidence、contraindication handling、demo-only 與 production path 分離。
2. `Security Architect`：審 PHI、AES/HTTPS、key handling、log 與 raw image 風險。
3. `Backend Architect`：審 `llmxx-server` API、SQLite state、RAG client、模組邊界。
4. `API Tester`：審 `/api/intake`、`/api/v1/rag/check`、sample payload、錯誤情境與 smoke test。
5. `Reality Checker`：最後審是否把 demo 當 production、是否有證據不足卻宣稱完成。

若只能派一個代理，選 `Clinical EBM Demo Verifier`。  
若能派三個，選 `Clinical EBM Demo Verifier`、`Security Architect`、`Backend Architect`。

## 2. 目前狀態與證據

| 區塊 | 目前狀態 | 證據路徑 | 判斷 |
|---|---|---|---|
| 本機 OCR client | 已可從 mock HIS 擷取 SOAP/Vital Signs，產出 formal server payload JSON | `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-client-local-ocr\HANDOFF.md` | 已匯入 final deliverable，可視為正式 client 起點 |
| Client 驗證 | 2026-06-14 verification suite 10/10 strict pass | `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-client-local-ocr\diagnostics\verification_suite\suite_report_20260614_175805.json` | OCR JSON 契約可先固定 |
| Client server config | 交付預設 endpoint 為 `http://127.0.0.1:8017/api/intake`，`send_enabled=true`，localhost HTTP allowed，AES disabled | `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-client-local-ocr\config\default_config.json` | 可直接接本機 llmxx-server demo port |
| llmxx-server | 目前只有註解，尚無 FastAPI `app` 與 `/api/intake` | `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-server\main.py` | 主要缺口 |
| RAG check API | 已有 `POST /api/v1/rag/check`，接 `dx/tx/hx/age/sex/labs/top_k` | `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\rag_core\core5_api\schemas.py`、`router.py` | server 可直接串 |
| LAVA 現有 tasks | 已有 `semantic_reconstruct`、`embedding_dense`、`ebm_generate`、`query_decompose`、`synthetic_ebm_candidate`、`claim_verify` | `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\lava\task_registry.py` | 不要重複新增既有職責 |

### 2.1 文件評分標準

本文件目前評分只看「規畫書與驗證測試表是否足以交給後續實作」，不要求已完成 server code 或已跑測試。9 分以上的標準如下：

| 面向 | 9 分以上條件 |
|---|---|
| Contract alignment | client payload、AES envelope、`/api/intake`、RAG `/check` 原生 `ebm_hits`、llmxx 對外 response adapter 都定義清楚 |
| Clinical safety | `green/yellow/orange`、禁忌症 force `orange`、source validation、claim verifier、demo verifier、evidence-backed display gate 都不可繞過 |
| Security/privacy | PHI minimization、diagnostics wrapper rejection、archive default off、HTTPS/AES/key/log policy、negative security tests 都具體 |
| Backend operability | SQLite schema、idempotency、event ordering、degraded response、RAG failure mapping、LAVA executor registration 都可照表實作 |
| Verification matrix | P0/P1 測試有輸入、預期結果、通過門檻；未實作前可作為後續 acceptance checklist |

9 分以上不代表已可上線，只代表「規畫與測試表品質足夠，後續實作不需再猜核心契約」。

## 3. 修改目標

### 3.1 第一階段 MVP

目標是讓一筆 client payload 可以完成完整來回：

```text
sample server_payload JSON
  -> llmxx-server /api/intake
  -> 最小化保存 clinical_sessions
  -> 解析 SOAP 成 dx/tx/hx
  -> 呼叫 ebm-rag /api/v1/rag/check
  -> 回傳 session_id、light_color、score、short_comment、rag_comments[].sources
```

MVP 不做：

- 不重寫 OCR client。
- 不重寫 RAG retrieval/indexing。
- 不把 deterministic calculator 放進 LAVA。
- 不把 demo synthetic evidence 當正式 clinical evidence。
- 不在第一階段做複雜 UI，只保留 API 與可檢查狀態。

### 3.2 第二階段 LAVA 加線

新增兩個與 llmxx 線路有關的 LAVA chat tasks：

1. `clinical_soap_parse`
   - 位置：建議在 `ebm-rag/lava/task_registry.py` 註冊，executor 可放 `ebm-rag/lava/matching_tasks/clinical_soap_parse.py`，或由 `llmxx-server` 透過 LAVA invoke 呼叫。
   - 職責：把 client 的 SOAP/Vital Signs/clinical_text 轉成 RAG 可吃的 `dx`、`tx`、`hx`、`case_context`。
   - 分數：輸出 `parse_confidence`、`missing_fields`、`uncertainty_flags`。
   - 限制：不得補造缺失事實；遇到模糊診斷要標 uncertain，不能硬填。

2. `llmaaj_adjudicate`
   - 位置：建議 executor 放 `ebm-rag/lava/matching_tasks/llmaaj_adjudicate.py`，由 `llmxx-server` 在 RAG 回來後呼叫。
   - 職責：比較醫師處置 `tx` 與 RAG evidence/comment 的語意一致性、衝突、證據支持度。
   - 分數：輸出 `semantic_alignment_score`、`evidence_support_score`、`conflict_score`、`risk_score`。
   - 限制：LLM 分數只是語意裁判，最後紅黃綠仍由 deterministic hard gate 決定。
   - Gate：不可取代既有 `claim_verify` / `demo_verifier`。只要 claim support < 0.85、unsupported claim、contradiction、source validation error，該 clinical comment 不可標示為 evidence-backed。

模組邊界：`llmxx-server` 對 client payload 負第一責任。`clinical_mapper.py` 必須先用 deterministic rule 產出 `dx/tx/hx/labs`；`clinical_soap_parse` 只能做 optional enrichment 或 uncertainty scoring。RAG core 與 LAVA executor 不可直接依賴 `llmxx-client-local-ocr.v0.1` 的 OCR payload shape。

## 4. 三層 LLM 職責定義

你前面提到的三層 LLM，我理解成這樣：

| 層級 | LAVA task | 做什麼 | 可以給什麼分數 | 不可以做什麼 |
|---|---|---|---|---|
| 第一層：臨床語意理解 | `clinical_soap_parse` | 從 SOAP/Vitals 理解病情，抽出 Dx/Tx/Hx | `parse_confidence`、完整度、模糊度 | 不可憑空補診斷或病史 |
| 第二層：RAG 溝通/證據生成 | 既有 `query_decompose`、`ebm_generate` | 查詢擴寫、依 Top-K evidence 生成 EBM comments | evidence confidence、source coverage、generation status | 不可繞過 retrieval 直接創造 evidence |
| 第三層：語意比對/裁判 | `llmaaj_adjudicate` | 比對醫師處置與 EBM evidence 的重疊、相似、衝突 | semantic alignment、support、conflict、risk | 不可覆蓋禁忌症 hard gate |

所以不是只有一個 LLM 會產生分數。比較安全的做法是：每層 LLM 都可給自己的輔助分數，但最終決策要有 deterministic gate 統一收斂。

## 5. llmxx-server 修改範圍

建議在 `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-server` 新增最小可維護結構：

```text
llmxx-server/
  main.py                FastAPI app 與 route include
  schemas.py             client payload、session、RAG response schema
  settings.py            endpoint、DB path、AES、timeout 設定
  crypto.py              AES-GCM unwrap / HTTPS policy
  state_db.py            SQLite clinical_sessions / events
  clinical_mapper.py     SOAP -> dx/tx/hx deterministic fallback
  rag_client.py          呼叫 ebm-rag /api/v1/rag/check
  adjudicator.py         呼叫 llmaaj_adjudicate 或 deterministic fallback
  response_builder.py    組合 client response
```

`settings.py` 必須把本機 demo 與正式環境分開：HTTP 只允許 `127.0.0.1` / `localhost`，任何非本機 PHI 傳輸必須 HTTPS/TLS。AES-GCM 不能取代 HTTPS；若啟用 AES，必須使用 `AES-256-GCM`、12-byte nonce、32-byte key，tag 可附在 ciphertext 或 envelope 欄位但格式要固定，nonce uniqueness 以 key scope 保證。`key_id` 對應 server key ring；缺 key、key 長度錯誤、nonce 重用疑慮或解密失敗時禁止 plaintext fallback，錯誤訊息不可回傳解密細節。AES key、patient UID HMAC key、LAVA provider key 要分離；正式環境優先走 OS secret store / secret manager，env var 僅作 local/dev fallback。若在 reverse proxy 後方，只信任受控 proxy 注入的 scheme header，不信任任意 `X-Forwarded-Proto`。

### 5.1 API endpoints

| Endpoint | 方法 | 職責 |
|---|---|---|
| `/api/health` | GET | public 只回 `ok/degraded`；DB path、RAG URL、key 狀態只給 admin diagnostics |
| `/api/intake` | POST | 只接 client formal payload，建立最小化 session，呼叫 RAG，回傳結果 |
| `/api/sessions/{session_id}` | GET | 查 session 狀態與最近一次結果 |
| `/api/sessions/{session_id}/events` | GET | 第二階段且 admin/auth 後才做，查 allowlisted pipeline event metadata |

第一階段 `/api/intake` 用同步 request/response 就好，不先做 SSE。`main.py` 目前註解寫到 LLM-1/LLM-2 串流，但現階段真正缺的是穩定 intake contract。

`/api/intake` schema 必須 `extra=forbid`，只接受 formal server payload 根欄位。要明確拒收 diagnostics wrapper 與任何 debug/image 欄位，例如 `diagnostics`、`ocr`、`raw_ocr_text`、`screenshot`、`image_b64`、`server_payload_path`、`capture`。server 不能信任 `zero_disk_image_io` 宣告；若 request 內出現 image bytes、base64 image 或 screenshot path，一律 400。

`/api/intake` 要支援兩種 request body：

- Plain formal payload：`schema_version="llmxx-client-local-ocr.v0.1"`。
- AES envelope：`schema_version="llmxx-client-local-ocr.encrypted.v0.1"`，包含 `algorithm`、`nonce_b64`、`ciphertext_b64`、可選 `key_id`。

HTTP 語意固定：

- payload invalid、unsupported schema、diagnostics wrapper、image payload、AES envelope malformed：回 400。
- AES key 缺失或 key 長度錯誤：server startup fail 或 503，不回 plaintext fallback。
- session 已建立但 RAG not ready / port down / timeout / LAVA unbound / malformed RAG JSON：回 200 或 202，body 帶 `status="degraded"` 或 `status="not_evaluable"`、`error_code`、`retryable`。這樣 OCR client 的 `raise_for_status()` 不會吃掉可展示的 degraded result。

固定 `error_code` enum：

| error_code | HTTP | status | retryable | 使用情境 |
|---|---:|---|---|---|
| `invalid_payload` | 400 | `failed` | false | JSON 格式錯、必要欄位缺失 |
| `unsupported_schema_version` | 400 | `failed` | false | `schema_version` 不支援 |
| `diagnostics_wrapper_rejected` | 400 | `failed` | false | 收到 diagnostics/debug wrapper |
| `image_payload_rejected` | 400 | `failed` | false | 收到 screenshot path、image bytes、base64 image |
| `aes_envelope_malformed` | 400 | `failed` | false | AES envelope 欄位缺失或格式錯 |
| `aes_key_unavailable` | 503 | `failed` | true | server 啟用 AES 但 key 不可用 |
| `client_session_conflict` | 409 | `failed` | false | 同 `client_session_id` 但 payload hash 不同 |
| `rag_not_ready` | 200/202 | `not_evaluable` | true | RAG `/check` 回 409 not_ready |
| `rag_timeout` | 200/202 | `degraded` | true | RAG timeout |
| `rag_unavailable` | 200/202 | `degraded` | true | RAG port down 或連線失敗 |
| `rag_malformed_json` | 200/202 | `not_evaluable` | true | RAG 回傳 JSON 不符合 adapter contract |
| `lava_unbound` | 200/202 | `degraded` | true | optional LAVA task 未綁定 |
| `llm_malformed_json` | 200/202 | `degraded` | true | LLM task 回 malformed JSON 並 fallback |

未來 SSE 不放在 Phase 1。Phase 1 只保留 JSON event log；若要串流，另開 `/api/sessions/{id}/events/stream`，定義 `Last-Event-ID`、heartbeat、terminal event、event type、依 `seq` replay 與斷線重連。

### 5.2 SQLite state

建議最少兩張表：

```text
clinical_sessions
  id
  client_session_id
  correlation_id
  payload_hash
  created_at
  updated_at
  completed_at
  payload_schema_version
  source
  input_origin
  zero_disk_image_io
  patient_uid_ref
  status
  error_code
  degraded_reason
  dx
  tx
  hx
  parse_confidence
  rag_request_json
  rag_response_json
  rag_query_id
  light_color
  final_score
  error

clinical_events
  id
  session_id
  seq
  created_at
  stage
  level
  event_type
  message
  error_code
  event_metadata_json
```

`status` 建議限定 enum：`received`、`parsed`、`rag_pending`、`completed`、`degraded`、`not_evaluable`、`failed`。`client_session_id` 要有 unique/idempotency 規則：同一 client session 重送時不可建立兩筆互相矛盾的完成紀錄，應回同一 server session 或建立可追蹤 retry relation。

Idempotency 規則：

| 情境 | 處理 |
|---|---|
| 同 `client_session_id`、同 payload hash、前次已完成 | 回同一 server session 與既有結果 |
| 同 `client_session_id`、同 payload hash、前次 degraded 且 retryable | 可重跑 RAG/LAVA，保留同一 correlation_id 並新增 retry event |
| 同 `client_session_id`、不同 payload hash | 回 409 `client_session_conflict`，不覆蓋既有 session |
| 無 `client_session_id` | server 產生 session id；不做 client idempotency，只用 correlation_id 追蹤 |
| retry relation | 用 `correlation_id` 串同一臨床事件的 retry，不把不同 payload 混成同一結果 |

SQLite operational settings：

- migrations idempotent。
- `PRAGMA journal_mode=WAL`。
- `PRAGMA busy_timeout`。
- 對 `client_session_id`、`payload_hash`、`created_at`、`status` 建 index。
- `client_session_id IS NOT NULL` 時，idempotency 以 `(client_session_id, payload_hash)` 判斷同 payload 重送；同 `client_session_id` 但 payload hash 不同必須回 409 `client_session_conflict`。
- 寫入 session、event、RAG response 要有 transaction boundary。
- event `seq` 必須單調遞增，供未來 replay / SSE 使用；資料表需約束 `UNIQUE(session_id, seq)`，且 seq allocation 必須在同一 SQLite transaction 內完成，避免並發重送時產生重複 seq。

`rag_request_json` / `rag_response_json` 只能保存最小化、redacted、allowlisted 結構。不得保存 raw SOAP、clinical_text、raw patient UID、headers、LLM prompt、API key、AES ciphertext/plaintext 或解密後完整 payload。

`patient_uid_ref` 規則：

- 若不需要跨 session 關聯，存 `null` 或固定 redacted marker。
- 若需要跨 session 關聯，只存 `HMAC-SHA256(patient_uid, server-side secret)`。
- 不可把 raw patient UID 寫進 DB、JSON、log、response；HMAC secret 與 AES key 必須分離。

`event_metadata_json` 只能放 allowlisted metadata，例如 stage、status、duration、error_code、counts。禁止放 PHI、完整 SOAP、clinical_text、RAG request、LLM prompt、headers、API key、AES ciphertext/plaintext 或 stack trace。

FastAPI middleware 與 exception handler 不得記錄 request body、response body、headers secret、stack trace 內的敏感內容。驗收時的 log scan 是最後檢查，不可取代程式層的禁止記錄規則。

正式 payload archive 預設關閉。若為 demo/debug 需要保存檔案，只能保存 server-side PHI scan 通過後的最小化 redacted payload，且需加密 at rest、設定 retention、限制 ACL，路徑命名改成：

```text
C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\shared_data\llmxx_redacted_payload_archive
```

不得使用 `llmxx_raw_json` 這種命名，避免實作者誤存原始 PHI。server 也要在驗收時掃描輸出與 log 目錄，確認沒有 PNG/JPG/BMP、base64 image 或 raw OCR diagnostics。

## 6. RAG/LAVA 修改範圍

### 6.1 保留既有 RAG API

`llmxx-server` 優先呼叫：

```text
POST http://127.0.0.1:33301/api/v1/rag/check
```

request body：

```json
{
  "dx": "migraine",
  "tx": "acetaminophen 500mg, follow up",
  "hx": "headache 3 days, pain 7/10; alert, no fever",
  "age": null,
  "sex": null,
  "labs": {
    "bp": "130/80",
    "hr": "76",
    "temp": "37.2",
    "rr": "16",
    "spo2": "98"
  },
  "top_k": 10
}
```

RAG `/check` 直接回 `run_query()` 的原生 `ebm_hits`，server adapter 不可假設它是簡化後的 `traffic_light/rag_query_id/source_refs`。目前要對齊的原生欄位是：

```json
{
  "query_id": "...",
  "status": "ok",
  "light_color": "green|yellow|orange",
  "llmaaj_score": 0.0,
  "short_comment": "...",
  "rag_comments": [
    {
      "comment": "...",
      "sources": [
        {
          "paper_id": "...",
          "chunk_id": "...",
          "pmid": "...",
          "doi": "..."
        }
      ]
    }
  ],
  "warnings": [],
  "retrieval": {
    "phases": [],
    "chunks": []
  }
}
```

`llmxx-server` 對外 response 可轉欄位名稱，但必須保存原生語意：

- `query_id` -> `ebm.rag_query_id`
- `light_color` -> `ebm.light_color`，全文件採 RAG 既有 `green/yellow/orange`，不另引入 `red`
- `llmaaj_score` -> `ebm.llmaaj_score`
- `rag_comments[].sources[]` -> `ebm.rag_comments[].sources[]`
- `warnings` 與 `retrieval` 必須可追蹤保存或透傳

RAG 回傳後，server 只做 adapter、包裝與加嚴裁判，不修改 RAG evidence。`response_builder.py` 只能 downgrade 或改成 `not_evaluable`，不可把 RAG 的 `orange/yellow/not_evaluable` 升級成 green。

Production `llmxx-server` 預設不啟用 synthetic fallback。demo fallback 必須由 explicit demo flag/env 啟用，response 必須含 `demo_only=true`、`demo_verifier.verdict`、`demo_verifier.score`、`demo_verifier.hard_fail_reasons`、`display_mode`；commercial build 可以移除 demo branch 而不影響 normal RAG。只有 `demo_verifier.verdict="pass"`、`demo_verifier.score >= 85`、`demo_verifier.hard_fail_reasons=[]`、`display_mode="evidence_backed"` 時，demo comment 才能 evidence-backed display；`display_mode="review"` 或 `verdict="reject"` 一律只能 review-only 或 blocked。

### 6.2 LAVA task registry 建議

新增 task 時要注意 capability：

```python
{
    "task_id": "clinical_soap_parse",
    "label": "Clinical SOAP Parse",
    "capability": "chat",
    "module": "lava.matching_tasks.clinical_soap_parse",
    "required": False,
    "description": "llmxx SOAP/Vital Signs -> Dx/Tx/Hx/case_context 語意解析"
}
```

```python
{
    "task_id": "llmaaj_adjudicate",
    "label": "LLMAAJ Adjudication",
    "capability": "chat",
    "module": "lava.matching_tasks.llmaaj_adjudicate",
    "required": False,
    "description": "比較 clinical Tx 與 RAG evidence/comment 的語意支持度與衝突"
}
```

第一階段建議都設 `required=False`，因為：

- server 要能在 LAVA 未綁定時用 deterministic fallback 回應。
- RAG readiness 不應因 optional adjudication 未綁定而失敗。
- Demo 時可以逐步打開，不會卡住基本 intake。

新增 task 不只改 `lava/task_registry.py`。若要透過 `/api/lava/tasks/{task_id}/invoke` 執行，還必須在 `lava/api_router.py` 的 `_TASK_EXECUTORS` 註冊 executor，否則 registry 有 task 但 invoke 仍會回 501。

## 7. Final decision gate

最後輸出建議分兩層：

1. LLM score layer
   - `parse_confidence`
   - `semantic_alignment_score`
   - `evidence_support_score`
   - `conflict_score`
   - `risk_score`

2. Deterministic gate layer
   - 禁忌症 / severe adverse / fatal interaction 命中：最終 `light_color` 必須是 `orange`，不可被 LLM 調回 green 或 yellow。
   - 沒有 source refs：不可 green，也不可標成 evidence-backed。
   - RAG unavailable / no chunks / unsafe chunks / source validation failed：一律 `not_evaluable`；yellow 只用於有真實 retrieved evidence 但不足以 green 的情境。
   - Clinical comment 必須能追到 `chunk_id`，且 `chunk_id` 必須存在於 retrieved chunks；PMID/DOI 若出現，必須與 chunk metadata 一致。
   - Retrieved chunk metadata 必須驗證 `paper_id`、`chunk_id`、`source_type`、`six_s_level`、`ocebm_level`、`specialty`、`disease`、`is_guideline`、`has_contraindication_terms`。疾病、治療、specialty 或 source metadata off-domain 時 fail closed / `not_evaluable`。
   - `claim_verify` / `demo_verifier` 未通過、claim support < 0.85、unsupported claim、contradiction、source validation error：block evidence-backed display。
   - Evidence-backed display 條件：`claim_verify.overall_claim_support >= 0.85`、`demo_verifier.verdict="pass"`、`demo_verifier.score >= 85`、`demo_verifier.hard_fail_reasons=[]`、`display_mode="evidence_backed"`、source validation pass、無 off-domain metadata、無 contraindication marker。
   - green 條件：source validation pass、高階 evidence 或合格 guideline/source、claim verifier pass、無 contradiction、無 contraindication marker。
   - LLM malformed JSON：使用 fallback 並標記 `llm_status=degraded`。
   - calculator 類醫療分數：只用本地 deterministic function，不用 LLM 算。

建議 response 外觀：

```json
{
  "ok": true,
  "session_id": "server-session-id",
  "client_session_id": "llmxx-...",
  "status": "completed",
  "clinical_parse": {
    "dx": "atrial fibrillation",
    "tx": "anticoagulation evaluation",
    "hx": "age, renal function, bleeding risk, current medications",
    "parse_confidence": 0.86
  },
  "ebm": {
    "light_color": "yellow",
    "short_comment": "Evidence exists but does not meet green criteria.",
    "rag_query_id": "...",
    "rag_comments": [
      {
        "comment": "...",
        "sources": [
          {
            "paper_id": "paper-...",
            "chunk_id": "chunk-...",
            "pmid": "...",
            "doi": "..."
          }
        ]
      }
    ],
    "warnings": [],
    "retrieval": {
      "phases": [],
      "chunks": []
    }
  },
  "adjudication": {
    "semantic_alignment_score": 0.72,
    "evidence_support_score": 0.65,
    "conflict_score": 0.12,
    "risk_score": 0.28
  },
  "claim_verify": {
    "overall_claim_support": 0.65,
    "verdict": "review"
  },
  "demo_verifier": {
    "verdict": "review",
    "score": 72,
    "hard_fail_reasons": []
  },
  "final_gate": {
    "light_color": "yellow",
    "reason": "Evidence exists but support is not strong enough for green.",
    "reason_codes": ["claim_support_below_evidence_backed_threshold"],
    "hard_fail_reasons": [],
    "display_mode": "review",
    "evidence_backed": false
  }
}
```

## 8. 實作順序

### Phase 1：server intake 骨架

1. 在 `llmxx-server/main.py` 建立 FastAPI app。
2. 新增 `schemas.py`，完整接 `llmxx-client-local-ocr.v0.1` payload。
3. 新增 `state_db.py`，建立 idempotent SQLite tables。
4. 新增 `/api/health`、`/api/intake`。
5. 用 `server_payload_175804_975370.json` 做 POST smoke test。

驗收：

- `/api/health` 200。
- `/api/intake` 可建立 session。
- malformed payload 會回 400，不會 crash。
- diagnostics wrapper / extra fields / image payload 會回 400。
- raw patient UID 不落 DB、JSON、log、response。
- server 端會做 PHI minimization / validation，不只信任 client redaction。
- 預設不保存完整 payload；若啟用 archive，只保存 server-redacted/minimized payload。
- server output/log/diagnostics 目錄沒有 PNG/JPG/BMP、base64 image、raw OCR text、SOAP/clinical_text dump、API key。
- 非 localhost HTTP 被拒；AES malformed envelope 被拒；key 缺失或長度錯誤不 plaintext fallback。

### Phase 2：接 RAG `/check`

1. 新增 `rag_client.py`，用 `httpx` 呼叫 RAG。
2. `clinical_mapper.py` 先用 deterministic mapping：
   - `A` -> `dx`
   - `P` -> `tx`
   - `S + O + vital_signs` -> `hx/labs`
3. `/api/intake` 呼叫 `/api/v1/rag/check`。
4. 儲存 `rag_query_id`、`light_color`、`rag_comments[].sources`、`warnings`、`retrieval`。

驗收：

- RAG 開著時能回 EBM result。
- RAG 關閉、RAG 409 not_ready、RAG timeout、malformed RAG JSON 時，`/api/intake` 回 200/202 degraded 或 not_evaluable，不遺失 session。
- RAG unavailable / no chunks / source validation failed 一律 `not_evaluable`，不可回可被誤解為 clinical review result 的 yellow。
- RAG adapter mapping test 覆蓋 `query_id`、`light_color`、`llmaaj_score`、`rag_comments[].sources`、`warnings`、`retrieval`。
- empty retrieval、generation failed、top_k clamp 1/50 都有 smoke case。
- `top_k` 限制在 1 到 50。

### Phase 3：新增 `clinical_soap_parse`

1. 在 LAVA registry 新增 optional task。
2. 新增 executor，輸出固定 JSON schema。
3. 在 `lava/api_router.py` 的 `_TASK_EXECUTORS` 註冊 executor。
4. server 若 task ready，就用 LLM parse enrichment；否則 deterministic fallback。
5. parse result 必須保留原始 SOAP 欄位 trace，但 trace 只能是欄位名、span/hash 或 redacted reference，不得保存 raw SOAP text。

驗收：

- LLM 回 malformed JSON 時 fallback。
- LLM 不可新增 payload 不存在的 facts。
- output 有 `parse_confidence` 與 uncertainty flags。

### Phase 4：新增 `llmaaj_adjudicate`

1. 在 LAVA registry 新增 optional task。
2. 在 `lava/api_router.py` 的 `_TASK_EXECUTORS` 註冊 executor。
3. executor 接收 `clinical_parse` + 原生 `ebm_hits` + `rag_comments[].sources`。
4. 產生語意分數與理由。
5. `response_builder.py` 套 deterministic hard gate。

驗收：

- 無 `rag_comments[].sources` 不可 green，也不可 evidence-backed display。
- contraindication marker 命中必須 force `orange`。
- `claim_verify` / `demo_verifier` 未通過時，不可 evidence-backed display。
- LLM 分數只影響 comment/score，不覆蓋 hard gate。

### Phase 5：client live send

1. server 穩定後，修改 client config：

```json
"network": {
  "endpoint_url": "http://127.0.0.1:8017/api/intake",
  "send_enabled": true
}
```

2. 本機 demo 只允許 `127.0.0.1` / `localhost` HTTP，且要 fail closed。
3. 非 localhost 或正式環境必須 HTTPS/TLS；AES-GCM 可作 application-layer encryption，但不能取代 HTTPS。

驗收：

- `Start-LocalOCR-System.ps1` 啟動 HIS + OCR client。
- Ctrl+Alt+G 後 server 收到 payload。
- client 仍保留 standalone `server_payloads` 輸出。

### 8.1 驗證測試矩陣

這張表是後續實作者必跑的 acceptance checklist。現在尚未實作，所以這裡只定義測試，不宣稱已通過。

P0 是 Phase 1 + Phase 2 完成前必須通過；P1 是新增 LAVA task 或 demo 展示前必須通過。

| ID | 優先級 | 類別 | 測試 | 輸入 / 情境 | 預期結果 |
|---|---|---|---|---|---|
| API-001 | P0 | Intake contract | formal payload 可接收 | `rootmedicals-a/llmxx-client-local-ocr/server_payloads/20260614/server_payload_175804_975370.json` | `/api/intake` 回 200/202，建立 session，產生 `clinical_parse`、`ebm`、`final_gate` |
| API-002 | P0 | Intake contract | diagnostics wrapper 被拒 | 任一 `diagnostics/verification_suite/*.json` 或含 `diagnostics/ocr/raw_ocr_text/capture/server_payload_path` 欄位的 body | 回 400，session 不進入 RAG，不保存 raw OCR/debug 欄位 |
| API-003 | P0 | Intake contract | extra fields 被拒 | formal payload 加上 `screenshot`、`image_b64`、`raw_ocr_text` | 回 400，log 不含 payload dump |
| API-004 | P0 | Intake contract | unsupported schema 被拒 | `schema_version="unknown"` | 回 400，錯誤格式固定為 `error_code` + safe message |
| SEC-001 | P0 | PHI minimization | raw patient UID 不落地 | payload 內放可辨識 patient_uid | DB、archive、log、response 只可有 `patient_uid_ref` 或 redacted marker |
| SEC-002 | P0 | Archive policy | payload archive 預設關閉 | 未開 demo/debug archive flag | 不建立 `llmxx_redacted_payload_archive` 檔案 |
| SEC-003 | P0 | Log hygiene | 掃描輸出與 log | server log、diagnostics、shared_data | 不含 raw SOAP dump、clinical_text dump、raw patient UID、API key、AES key、PNG/JPG/BMP/base64 image |
| SEC-004 | P0 | HTTPS policy | 非 localhost HTTP | endpoint host 不是 `127.0.0.1` / `localhost` 且 scheme 是 HTTP | fail closed，不能送 PHI |
| SEC-005 | P0 | AES envelope | malformed envelope | 缺 `nonce_b64`、錯誤 key 長度、ciphertext 非 base64 | 回 400 或 startup fail；不可 plaintext fallback，不回解密細節 |
| DB-001 | P0 | SQLite migration | 初始化與重跑 migration | 空 DB、既有 DB 各跑一次 | migration idempotent，`clinical_sessions`、`clinical_events` 存在，WAL、busy_timeout、indexes 已設定 |
| DB-002 | P0 | Idempotency | 同一 client_session_id 重送 | 同 payload POST 兩次 | 不建立互相矛盾完成紀錄；回同一 session 或可追蹤 retry relation |
| DB-003 | P0 | Event ordering | event seq 單調 | 同一 session 多階段事件 | `seq` 單調遞增，可 replay，terminal event 明確 |
| DB-004 | P0 | Idempotency conflict | 同 client_session_id 不同 payload | 第二次 POST 使用相同 `client_session_id` 但不同 payload hash | 回 409 `client_session_conflict`；不得覆蓋既有 session/result |
| RAG-001 | P0 | RAG adapter | RAG ready 成功 mapping | RAG `/check` 回原生 `ebm_hits` | adapter 保留 `query_id`、`light_color`、`llmaaj_score`、`rag_comments[].sources`、`warnings`、`retrieval` |
| RAG-002 | P0 | RAG degraded | RAG 409 not_ready | RAG readiness 未通過 | `/api/intake` 回 200/202 `status=not_evaluable|degraded`、`error_code`、`retryable`，client 可拿到 body |
| RAG-003 | P0 | RAG degraded | RAG port down / timeout | RAG server 未啟動或 timeout | session 保留，回 200/202 degraded/not_evaluable，不回 5xx 給 OCR client 當唯一結果 |
| RAG-004 | P0 | Retrieval safety | no chunks / source validation failed | RAG 回 empty retrieval 或 source validation error | final `status=not_evaluable`，不可 yellow/green，不可 evidence-backed display |
| RAG-005 | P0 | Adapter robustness | malformed RAG JSON | RAG 回非預期 JSON | session 保留，回 degraded/not_evaluable，event 記錄 safe error_code |
| EBM-001 | P0 | Citation integrity | source trace | 每個 clinical comment | 每個 displayed clinical comment 都能 trace 到 retrieved `chunk_id`；PMID/DOI 與 chunk metadata 一致 |
| EBM-002 | P0 | Hard gate | contraindication marker | retrieved chunk 或 patient context 有 hard contraindication / severe adverse / fatal interaction | final `light_color="orange"`，不可被 LLM 改成 yellow/green |
| EBM-003 | P0 | Evidence-backed gate | claim verifier fail | `claim_verify` support < 0.85、unsupported、contradiction 或 source validation error | block evidence-backed display，必要時 downgrade/not_evaluable |
| EBM-004 | P1 | Demo boundary | synthetic fallback | synthetic candidate 觸發 | production 預設 disabled；demo only 必須帶 `demo_only=true`、`demo_verifier.verdict`、`display_mode` |
| EBM-005 | P0 | Citation integrity | missing chunk | `rag_comments[].sources[].chunk_id` 不存在於 retrieved chunks | `not_evaluable` 或 block display，記錄 `reason_codes=["source_chunk_missing"]` |
| EBM-006 | P0 | Citation integrity | PMID/DOI mismatch | source PMID/DOI 與 chunk metadata 不一致 | block evidence-backed display，記錄 `reason_codes=["source_metadata_mismatch"]` |
| EBM-007 | P0 | Domain safety | off-domain evidence | disease、treatment、specialty、source_type metadata 與 clinical query 不一致 | fail closed / `not_evaluable`，不可 yellow/green |
| EBM-008 | P0 | Demo verifier gate | review/reject display | `demo_verifier.verdict="review"` 或 `reject`，或 score < 85 | `display_mode` 不可為 `evidence_backed`，`evidence_backed=false` |
| EBM-009 | P0 | Claim verifier availability | unbound/malformed claim verifier | `claim_verify` unbound 或回 malformed JSON | fail closed；不可 evidence-backed display，必要時 `degraded` 或 `not_evaluable` |
| EBM-010 | P0 | Metadata completeness | required metadata missing | retrieved chunk 缺 `six_s_level`、`ocebm_level`、`is_guideline` 或 `has_contraindication_terms` 等必要 metadata | `not_evaluable`，不可 evidence-backed display |
| EBM-011 | P0 | Chunk quality gate | unsafe chunk quality | chunk quality gate fail、unsafe chunks、或 retrieval quality below threshold | 不得 yellow/green；不得 evidence-backed display |
| LAVA-001 | P1 | Registry | 新 task 可見 | 新增 `clinical_soap_parse`、`llmaaj_adjudicate` | LAVA task binding UI/API 可見，`required=False` |
| LAVA-002 | P1 | Executor | invoke 不回 501 | `/api/lava/tasks/clinical_soap_parse/invoke`、`/api/lava/tasks/llmaaj_adjudicate/invoke` | `_TASK_EXECUTORS` 已註冊，未綁定時回可預期 unconfigured，不阻塞 readiness |
| LAVA-003 | P1 | Optional fallback | task unbound | 不綁定新增 tasks | server 使用 deterministic fallback；RAG readiness 不失敗 |
| LAVA-004 | P1 | Malformed LLM | LLM 回 malformed JSON | parse/adjudication executor | fallback，`llm_status=degraded`，不寫入 unsupported clinical fact |
| CLIN-001 | P0 | Deterministic mapper | SOAP mapping | `A/P/S/O/vital_signs` 欄位齊全 | `A -> dx`、`P -> tx`、`S+O -> hx`、vitals -> `labs`，不讓 RAG core 依賴 OCR schema |
| CLIN-002 | P1 | Clinical parser | 不造假 | SOAP 缺 A 或 P | `missing_fields` / uncertainty flags 明確，不憑空補診斷或處置 |
| QA-001 | P0 | OpenAPI | schema 產生 | 啟動 `llmxx-server` | OpenAPI 可生成，`/api/intake` request/response schema 穩定 |
| QA-002 | P0 | Response snapshot | degraded snapshot | RAG down、timeout、malformed RAG JSON | snapshot 有 `status`、`error_code`、`retryable`、`session_id`，無 PHI |
| QA-003 | P0 | Top-K clamp | top_k 邊界 | `top_k=0`、`top_k=999` | clamp 到 1/50，event 記錄 safe metadata |

P0 全部通過後，Phase 1 + Phase 2 才算可進入 demo 串接。P1 全部通過後，才可以展示新增 LAVA task 的三層 LLM 分數線路。

## 9. 審查員角色建議

### 9.1 主審：Clinical EBM Demo Verifier

審查重點：

- RAG evidence 是否都有 source refs。
- 每個 clinical comment 是否能 trace 到 retrieved `chunk_id`。
- `claim_verify` 與 `synthetic_ebm_candidate` 是否仍只限 demo。
- contraindication / hard gate 是否沒有被 LLM 分數覆蓋。
- `light_color` 是否遵守 `green/yellow/orange`，禁忌症是否 force `orange`。
- evidence-backed display 是否通過 source validation、claim verifier、demo verifier gate。
- 規畫書是否把 clinical safety 放在 API 成功之前。

推薦原因：這個角色名稱與描述直接對 RootMedicals RAG demo verifier 對齊，是最貼近目前文件風險的人選。

### 9.2 安全審：Security Architect

審查重點：

- AES-GCM key 是否正式環境走 OS secret store / secret manager、local/dev 才用 env var fallback，且不進 log、不進 response。
- localhost HTTP 與 production HTTPS 的界線是否明確。
- PHI、patient_uid、redaction、diagnostics 是否符合最小化原則。
- raw screenshot 是否不落地。
- payload archive 是否預設關閉；若啟用，是否只存 encrypted server-redacted/minimized payload。

### 9.3 架構審：Backend Architect

審查重點：

- `llmxx-server` 模組切分是否夠小。
- `/api/intake` response 是否能支援同步 MVP 與未來 event log。
- SQLite migration 是否 idempotent。
- RAG unavailable 時是否用 200/202 degraded/not_evaluable 讓 client 拿到結果。
- client/server/RAG contract 是否清楚。
- RAG 原生 `ebm_hits` 與 llmxx 對外 response adapter 是否分清楚。

### 9.4 測試審：API Tester

審查重點：

- sample payload smoke test 是否足夠。
- malformed JSON、缺 `dx`、RAG down、LAVA unbound、timeout 是否都有案例。
- `/api/health` 是否能檢查 DB/RAG。
- response schema 是否穩定。

### 9.5 現實審：Reality Checker

審查重點：

- 哪些是已完成，哪些只是規畫。
- 是否把 OCR 10/10 誤解成臨床 EBM 已驗證。
- 是否有「看起來有分數」但沒有 evidence 的假完成。
- 是否有 production-ready 宣稱過早。

## 10. 建議代理審查流程

若使用本機代理角色審查本文件，建議順序：

1. `Clinical EBM Demo Verifier` 先審 clinical/RAG/LAVA 安全線。
2. `Security Architect` 同步審 PHI/AES/HTTPS/log。
3. `Backend Architect` 同步審 API/DB/模組邊界。
4. `API Tester` 根據前三者意見補驗收測試。
5. `Reality Checker` 最後做 go/no-go 語氣校正，避免規畫書過度承諾。

給代理的共用審查問題：

```text
請審查 C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\doc\LLMXX_LAVA_RAG_MODIFICATION_PLAN_2026-06-15.md。
請只根據文件與本機專案證據指出：
1. 哪些修改順序有風險？
2. 哪些 LAVA task 職責混淆或重複？
3. 哪些醫療安全、隱私、安全、API 契約、驗證條件不足？
4. 哪些地方應降級為 demo-only 或 future work？
請用「阻塞 / 重要 / 建議」三類輸出，並附上應修改的段落。
```

## 11. 風險與處理

| 風險 | 影響 | 處理 |
|---|---|---|
| llmxx-server 還是空殼 | client 無法正式送件 | 先做 `/api/intake` MVP，不先做 UI |
| RAG 文獻覆蓋不足 | 回答可能無 evidence 或不足以 green | 無 `rag_comments[].sources` 不可 green；RAG unavailable / no chunks / source validation failed 回 `not_evaluable` |
| LLM 分數被誤當最終判斷 | 醫療安全風險 | hard gate 放在 final decision；server 只能 downgrade 不可 upgrade |
| AES/HTTPS 邊界不清 | 正式部署安全風險 | localhost demo 與 production 設定分離；非 localhost 必須 HTTPS/TLS |
| OCR 10/10 被過度解讀 | 只證明 client payload，不證明 EBM 正確 | 文件與 response 明確標示 verification scope |
| demo synthetic evidence 混入正式線路 | citation integrity 風險 | `synthetic_ebm_candidate` 只允許 demo/verifier gate |

## 12. 修改完成定義

第一階段完成定義：

- `llmxx-server` 有可啟動 FastAPI app。
- `/api/health` public 只回 `ok/degraded`，不洩漏 DB path、RAG URL、key 狀態。
- `/api/intake` 可接一筆 client formal payload。
- server 可最小化保存 session，預設不保存完整 payload archive。
- server 可呼叫 RAG `/api/v1/rag/check`。
- response 有 `session_id`、`clinical_parse`、`ebm`、`final_gate`。
- RAG down / LAVA unbound / RAG malformed JSON 回 200/202 `degraded` 或 `not_evaluable`，client 可拿到結果；payload invalid 才 400。
- diagnostics wrapper、extra fields、image payload、raw patient UID 落地、log 含 SOAP/API key 都有負向測試。

第二階段完成定義：

- LAVA task registry 顯示 `clinical_soap_parse` 與 `llmaaj_adjudicate`。
- `lava/api_router.py` 的 `_TASK_EXECUTORS` 已註冊兩個 executor，invoke 不回 501。
- optional task 未綁定時不阻塞 RAG readiness。
- 綁定後可產生 parse/adjudication 分數。
- final gate 保持 deterministic；禁忌症 force `orange`；claim verifier / demo verifier 未通過不可 evidence-backed display。

## 13. 下一步

建議下一個 Codex 實作任務只做 Phase 1 + Phase 2：

```text
請依照 doc/LLMXX_LAVA_RAG_MODIFICATION_PLAN_2026-06-15.md，
只實作 llmxx-server Phase 1 + Phase 2：
FastAPI /api/health、/api/intake、SQLite state、client payload schema、RAG /api/v1/rag/check client。
不要修改 OCR client，不要新增 LAVA task，不要做 UI。
完成後用 rootmedicals-a/llmxx-client-local-ocr/server_payloads/20260614/server_payload_175804_975370.json 做 smoke test。
```

LAVA 新增 task 應等 Phase 1 + Phase 2 的 server contract 先穩定，再讓代理審查本文件後執行。
