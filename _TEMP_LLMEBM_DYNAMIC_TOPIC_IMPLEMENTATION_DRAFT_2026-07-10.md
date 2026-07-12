<!--
  檔案路徑: rootmedicals-a/_TEMP_LLMEBM_DYNAMIC_TOPIC_IMPLEMENTATION_DRAFT_2026-07-10.md
  產生時間: 2026-07-10 +08:00
  版本: v0.1-temporary-draft
  說明: llmebm 動態 Topic Page：DOM＋截圖掃描、三階段 LAVA/RAG 與內容回填實作草稿。
  生命週期: 此檔只供實作與驗收追蹤；全部驗收通過並留下正式 verification report 後必須刪除。
  安全: 不包含 API key、登入 cookie、DynaMed 內容、病人資料或原始參考網站截圖。
  ----------------------------------------------------------------------------------------------------
-->

# TEMP — llmebm 動態 Topic Page 實作草稿

> 狀態：核心 runtime 已實作並通過 AF live E2E；DynaMed reference mode、既有 UI placeholders 與使用者最終驗收仍待完成。  
> 邊界：`rootmedicals-a/llmebm`、`rootmedicals-a/ebm-rag`、LAVA task binding。  
> 刪除條件：第 13 節驗收全數通過、使用者確認驗收、正式驗證報告已保存後，刪除此檔。

## 1. 已確認的目標

本功能不是在正式運行時持續掃 DynaMed，也不是複製 DynaMed 內容。

流程分成兩段：

1. 開發期參考掃描
   - 使用已授權的瀏覽器 session，以 Scrapling／Playwright 取得 DynaMed 的 DOM、截圖與互動狀態。
   - 只提取一般性醫學頁面結構、標題關係、元件類型與互動規律。
   - 不保存或搬運 DynaMed 醫學內文、圖像、品牌資產、登入資料或特殊措辭。
   - 產出經人工確認的 llmebm 自有版型規則後，DynaMed 不再是 runtime dependency。

2. llmebm 正式運行
   - 掃描自己的 llmebm Topic Page DOM 與截圖，建立目前頁面的 slot manifest。
   - LLM 1 根據 manifest 規劃各 slot 需要的資料。
   - LLM 2／RAG 根據需求拆查詢、檢索自己的文獻與 evidence chunks。
   - LLM 3 只根據 LLM 1 的需求與 RAG evidence 編排內容 blocks。
   - llmebm 以 deterministic renderer 將 blocks 填入自己的 DOM。

「動態」的定義是：頁面當下有哪些標題與內容槽，就為那些槽動態決定資料需求、檢索證據、生成內文並回填；不是每次開頁重新掃 DynaMed。

## 2. 不做的事情

- 不在每次開啟疾病頁時完整掃描 DOM 或呼叫三個 LLM。
- 不建立第二套 LAVA provider／API key／binding 管理。
- 不改寫既有 Core0-Core5 文獻 ingestion、embedding 或 Qdrant indexing。
- 不用 LLM 計算可由 deterministic calculator 完成的臨床分數。
- 不讓 LLM 輸出任意 HTML、JavaScript、CSS selector 或可執行程式碼直接進頁面。
- 不把 `ebm_generate` 的病人查證／紅綠燈 schema 硬改成 Topic Page schema。
- 不新增排程佇列、Redis、Celery 或前端框架；MVP 使用現有 FastAPI、SQLite 與原生 JavaScript。
- 不自動監控 DynaMed 改版。若未來需要比較新版型，才人工啟動參考掃描。

## 3. 現況與缺口

| 需求 | 目前行為 | 證據 | 缺口 | 風險／驗證 |
| --- | --- | --- | --- | --- |
| 靜態標題樹 | 已有 `UNIVERSAL_TEMPLATE` 與 custom sidebar nodes | `llmebm/app/model/sidebar_model.py` | 節點缺 stable `slot_id`、`content_target` 與 content status | 改名後 slot 不可換 ID；同名節點不可撞 ID |
| 點標題顯示內容 | `specialty.html` 已有中間 content pane | `llmebm/app/templates/specialty.html` | 現在只以 `innerHTML` 塞 mock 文字 | LLM 內容若進 `innerHTML` 會有 XSS／破版風險 |
| llmebm RAG 流程 | `pipeline_event_generator()` 是固定 sleep 與固定 AAA 範例 | `llmebm/main_ebm.py` | 不是真 RAG，也不是 Topic Page 契約 | 不得把新流程塞進 `/analyze-soap` 假裝完成 |
| 查詢策略 | 已有 `rag_query_strategy`、`query_decompose` 與 deterministic fallback | `ebm-rag/lava/task_registry.py`、`rag_core/core4_ragging/retriever.py` | 需要接收 section plan，逐 slot 建立 retrieval context | 既有病例查詢不可退化 |
| Evidence retrieval | `retrieve()` 已有 embedding、Qdrant、metadata filter、三階段 fallback | `rag_core/core4_ragging/retriever.py` | 缺 Topic Page orchestrator 與 section coverage 統計 | 每筆引用必須能追到 retrieved chunk |
| 內容生成 | `ebm_generate` 產生 `ebm_hits`／traffic light 候選 | `lava/matching_tasks/ebm_generate.py` | Topic Page 需要不同的 blocks schema | 不應污染臨床 check 契約，新增窄 task |
| LAVA 視覺輸入 | adapter 目前 `chat()` 只保證文字訊息；verified capability 只有 chat／embedding | `lava/adapter/base.py`、`lava/schemas.py` | LLM 1 必須能接 DOM JSON＋PNG/JPEG | 未配置 vision 時明確 `unconfigured`，不可 silent mock |
| Topic content persistence | 目前只有 taxonomy/sidebar SQLite | `llmebm/app/model/ebm_model.py`、`sidebar_model.py` | 缺 manifest、slot content、status、version、evidence digest | 重跑不可覆蓋最後一版可用內容 |
| 自有頁面掃描 | 尚無 Scrapling 或 browser scanner | `llmebm/requirements.txt` | 需獨立、可重跑、只掃 allowlisted URL 的工具 | 防 SSRF、限制深度／大小、截圖不可含 PHI |

## 4. 目標資料流

```text
開發期一次性 DynaMed 參考掃描（授權 session）
  -> DOM／互動 state graph／screenshots
  -> 人工確認一般性 layout rules
  -> 完成 llmebm 自有 template 與 data-slot contract

llmebm 結構新增或修改
  -> scan_topic_manifest.py 掃自己的 /topic/{topic_name}
  -> DOM slots + screenshot(s) + dom_hash
  -> llmebm SQLite 儲存 current manifest
  -> POST ebm-rag /api/v1/rag/topic-content/generate
       -> LAVA: topic_content_plan（LLM 1，vision）
       -> existing rag_query_strategy/query_decompose + retrieve（LLM 2／RAG）
       -> LAVA: topic_content_compose（LLM 3，chat）
       -> deterministic schema/source validation
  -> llmebm SQLite 原子更新成功 slots
  -> 前端點標題時 GET 該 slot content
  -> deterministic renderer 建立 DOM nodes
```

## 5. 掃描觸發規則

DOM 掃描不是排程，而是事件觸發：

| 事件 | 動作 |
| --- | --- |
| 新疾病頁第一次建立 | 掃一次，建立 manifest |
| Admin 新增／刪除／移動／改名標題 | 儲存成功後標記 manifest dirty，掃一次 |
| llmebm template 或 slot contract 版本變更 | 部署後掃一次 |
| 人工要求重新掃描 | 強制掃一次 |
| 一般開頁／點標題 | 不掃；讀 current manifest 與 content cache |

每次開頁只比較便宜的結構版本／hash。hash 未變，不啟動 Scrapling，也不呼叫 LLM。

RAG 內容更新與 DOM 掃描分離：新文獻若使 evidence revision 改變，只把相關 slot 標為 `stale`；不重掃 DOM。重新生成成功前保留舊內容並標示更新中。

## 6. Stable slot contract

### 6.1 Slot ID

slot ID 不得由標題文字 slug 產生，否則改名會使內容與歷史斷鏈。

建議格式：

```text
{topic_uid}:universal:{universal_node_id}
{topic_uid}:custom:{sidebar_node_db_id}
```

範例：

```text
AAA-UID:universal:u3-2-2
AAA-UID:custom:41
```

Universal 與 custom node API 必須回傳：

```json
{
  "id": "u3-2-2",
  "slot_id": "AAA-UID:universal:u3-2-2",
  "name": "Imaging",
  "layer": 3,
  "content_target": true,
  "children": []
}
```

### 6.2 DOM attributes

所有可顯示內容的標題必須有穩定屬性：

```html
<button
  type="button"
  class="sb-link"
  data-slot-id="AAA-UID:universal:u3-2-2"
  data-slot-level="3"
  data-content-status="empty"
>
  Imaging
</button>
```

群組標題若同時可展開又可讀內容，必須將「選取標題」與「展開 children」拆成兩個可存取按鈕，不用單一 click 同時猜兩種意圖。

內容容器只保留一個 active slot：

```html
<article
  id="main-reading-content"
  data-active-slot-id="AAA-UID:universal:u3-2-2"
  aria-live="polite"
></article>
```

## 7. 四個 JSON 契約

### 7.1 Topic manifest：`llmebm-topic-manifest.v1`

```json
{
  "schema": "llmebm-topic-manifest.v1",
  "topic_uid": "AAA-UID",
  "topic_name": "Abdominal Aortic Aneurysm",
  "template_version": "condition.v1",
  "dom_hash": "sha256:...",
  "viewport": {"width": 1440, "height": 1000},
  "screenshots": [
    {"sha256": "...", "mime_type": "image/png", "state": "sidebar-expanded"}
  ],
  "slots": [
    {
      "slot_id": "AAA-UID:universal:u3-2-2",
      "heading": "Imaging",
      "heading_path": ["Diagnosis", "Testing", "Imaging"],
      "level": 3,
      "order": 7,
      "content_target": true,
      "allowed_blocks": ["summary", "recommendations", "bullets", "evidence_note", "table", "warning"]
    }
  ]
}
```

`dom_hash` 只包含會影響內容槽的 canonical fields；CSS class 排序、時間文字或既有內文不得造成 hash 改變。

### 7.2 LLM 1 plan：`llmebm-topic-plan.v1`

```json
{
  "schema": "llmebm-topic-plan.v1",
  "topic_uid": "AAA-UID",
  "manifest_hash": "sha256:...",
  "sections": [
    {
      "slot_id": "AAA-UID:universal:u3-2-2",
      "evidence_needs": ["indications", "preferred modality", "diagnostic findings", "limitations"],
      "query_intents": ["guideline", "diagnosis", "safety"],
      "filters": {"disease": "Abdominal Aortic Aneurysm"},
      "top_k": 10,
      "block_types": ["summary", "recommendations", "evidence_note"]
    }
  ]
}
```

Validator 必須拒絕：未知 slot、重複 slot、未知 block type、空 evidence needs、任意 URL／HTML／script、過大的 top_k。

### 7.3 Evidence bundle：`llmebm-evidence-bundle.v1`

```json
{
  "schema": "llmebm-evidence-bundle.v1",
  "slot_id": "AAA-UID:universal:u3-2-2",
  "query_id": "uuid",
  "queries": ["..."],
  "phases": ["phase1_strict_preferred"],
  "coverage": {"status": "complete", "missing_needs": []},
  "hits": [
    {
      "paper_id": "...",
      "chunk_id": "...",
      "title": "...",
      "source_type": "guideline",
      "six_s_level": "Summaries",
      "ocebm_level": "1",
      "is_guideline": true,
      "score": 0.91,
      "text": "..."
    }
  ]
}
```

必須沿用既有 retriever 的 strict-first、insufficient-hits 才 widen、每 phase 留紀錄的行為。

### 7.4 LLM 3 content：`llmebm-topic-content.v1`

```json
{
  "schema": "llmebm-topic-content.v1",
  "slot_id": "AAA-UID:universal:u3-2-2",
  "status": "ready",
  "blocks": [
    {
      "type": "summary",
      "text": "...",
      "citations": [{"chunk_id": "...", "paper_id": "..."}]
    },
    {
      "type": "bullets",
      "items": [
        {"text": "...", "citations": [{"chunk_id": "...", "paper_id": "..."}]}
      ]
    }
  ],
  "missing_evidence": [],
  "model": {"provider": "...", "model_id": "..."}
}
```

每個臨床主張都必須至少有一個 citation；citation 的 `chunk_id` 與 `paper_id` 必須存在於該 slot evidence bundle。無足夠 evidence 時輸出 `status=insufficient_evidence`，不可補模型常識。

## 8. LAVA 任務設計

### 8.1 新增 `topic_content_plan`

- capability：`vision`
- required：`false`（不可讓未配置 Topic 功能破壞既有 RAG `/health`）
- Topic endpoint 自己把它視為必需。
- 輸入：manifest JSON、最多 3 張 PNG/JPEG、topic metadata。
- 輸出：嚴格 `llmebm-topic-plan.v1`。
- 職責：判斷每個 slot 需要的資料與合適 block types；不回答醫學問題。

目前 adapter 沒有正式 vision contract，因此需小幅擴充：

- `BaseLavaAdapter.supports_vision = False`
- `vision(api_key, model_id, prompt, images)` 預設 `NotImplementedError`
- `verify_vision()` 使用無敏感資料的小型測試圖驗證模型能力
- OpenAI-compatible 與 Google adapter 先實作 vision；其他 provider 未實作就不可綁此 task
- `VerifyRequest.capability` 加入 `vision`
- readiness 支援 `vision`

`verified_capability` 現階段仍是一個值。若同一模型同時要綁 vision 與 chat，先建立兩個 connection rows；不在本次順便重做 capability set migration。

### 8.2 重用既有 `rag_query_strategy`／`query_decompose`

- 將 `topic_name + heading_path + evidence_needs` 組成 section retrieval context。
- 在不破壞原 payload 的前提下，允許 optional `section_context`。
- 驗證後的 extra queries 與 deterministic base queries 合併。
- 實際 embedding、Qdrant、filters、fallback 仍由既有 `retrieve()` 完成。
- LLM query expansion 失敗時回 deterministic，不可讓整個 slot 失敗。

### 8.3 新增 `topic_content_compose`

- capability：`chat`
- required：`false`；Topic endpoint 自己要求 ready。
- 輸入：單一 section plan＋該 section evidence bundle。
- 輸出：嚴格 `llmebm-topic-content.v1`。
- 只允許 evidence-backed blocks，不產生 traffic light，不做病人治療裁判。
- 不能重用 `ebm_generate` output schema；但重用相同 adapter、binding、JSON extraction 與 safe error pattern。

### 8.4 Registry／readiness

在 `lava/task_registry.py` 新增兩個 task，在 `lava/api_router.py::_TASK_EXECUTORS` 註冊。兩者 globally optional，新增一個 Topic 專屬 readiness 檢查：

```json
{
  "ready": false,
  "required_tasks": {
    "topic_content_plan": {"ready": false, "reason": "..."},
    "embedding_dense": {"ready": true},
    "topic_content_compose": {"ready": true}
  }
}
```

不得用 mock 讓 readiness 看起來成功。

## 9. 服務與 API 邊界

llmebm 不直接讀取 LAVA DB 或 provider key。它只呼叫 ebm-rag 的 Topic application endpoint。

### 9.1 ebm-rag 新 endpoint

```text
POST /api/v1/rag/topic-content/generate
```

Request：

```json
{
  "manifest": {"schema": "llmebm-topic-manifest.v1"},
  "screenshots": [
    {"mime_type": "image/png", "image_b64": "...", "state": "sidebar-expanded"}
  ],
  "only_slot_ids": [],
  "filters": {},
  "top_k": 10
}
```

Response：

```json
{
  "status": "ok",
  "manifest_hash": "sha256:...",
  "plan": {"schema": "llmebm-topic-plan.v1"},
  "sections": [
    {
      "slot_id": "...",
      "status": "ready",
      "query_id": "...",
      "content": {"schema": "llmebm-topic-content.v1"}
    }
  ],
  "errors": []
}
```

限制：

- manifest body 設定上限。
- 最多 3 張圖；只接受 PNG/JPEG；解碼後單張最多 5 MB。
- 不把 image base64 寫入 retrieval log 或 error response。
- `only_slot_ids` 必須是 manifest 內的 slot。
- 每 slot 各自成功／失敗；單一 slot 失敗不得抹掉其他成功結果。

### 9.2 llmebm endpoints

```text
GET  /api/v1/topic/{topic_name}/manifest
GET  /api/v1/topic/{topic_name}/content/{slot_id}
POST /api/v1/topic/{topic_name}/content/generate
GET  /api/v1/topic/{topic_name}/content/status
```

MVP 不提供任意 URL 的 scan HTTP endpoint，以免 SSRF。掃描由本機 CLI 工具執行；內容 generate endpoint 只接受已保存 manifest 中的 slot IDs。

`POST .../content/generate`：

- 預設只生成 `empty`、`stale`、`failed` slots。
- `force=true` 也不得先刪除 ready 內容。
- 先寫 pending job／新版本，完整驗證成功後才以 transaction 切換 current version。
- RAG/LAVA 失敗時保留舊 content，回可理解的 `unconfigured`／`unavailable`／`insufficient_evidence`。

llmebm 呼叫 ebm-rag 優先使用 Python stdlib `urllib.request` 配合 `asyncio.to_thread`，避免只為一個 JSON endpoint 新增 runtime dependency；只有確認需要 streaming／connection pooling 才改用已安裝依賴。

## 10. SQLite persistence

新增 `llmebm/app/model/topic_content_model.py`，使用獨立 `data/system/topic_content.db`，不要把生成內容塞入 sidebar schema。

最小三表：

```sql
CREATE TABLE IF NOT EXISTS topic_manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_uid TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    template_version TEXT NOT NULL,
    dom_hash TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    screenshot_sha256_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(topic_uid, dom_hash)
);

CREATE TABLE IF NOT EXISTS topic_content_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_uid TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    plan_json TEXT,
    content_json TEXT,
    query_id TEXT,
    evidence_digest TEXT,
    model_json TEXT,
    error_json TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_generation_jobs (
    job_id TEXT PRIMARY KEY,
    topic_uid TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    requested_slot_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

必要索引：current slot lookup、topic jobs、manifest current lookup。migration 必須 idempotent。

MVP 可使用 process-local `asyncio.create_task` 執行 generation，但 job state 必須先落 SQLite；服務重啟時將殘留 `running` job 標成 `interrupted`，不可永遠 pending。

實作時需留下：

```python
# ponytail: process-local jobs only support one llmebm instance; move to a durable queue before multi-instance deployment.
```

## 11. Scanner 草案

新增：

```text
llmebm/tools/scan_topic_manifest.py
llmebm/requirements-scan.txt
```

`requirements-scan.txt` 只放：

```text
scrapling[fetchers]
```

不把 Chromium／Scrapling 塞進 llmebm runtime requirements 或 production container，除非後續明確要求 container 內掃描。

### 11.1 自有頁面模式

範例：

```powershell
python llmebm/tools/scan_topic_manifest.py `
  --url "http://127.0.0.1:8000/topic/Abdominal%20Aortic%20Aneurysm" `
  --output-root "llmebm/data/runtime/topic_scans"
```

固定流程：

1. 只允許設定檔中的 llmebm origin，預設 `127.0.0.1`／`localhost`。
2. 使用 `DynamicSession`，`disable_resources=False`、`network_idle=True`。
3. 等待 sidebar API 完成並出現 `[data-slot-id]`。
4. 展開所有 sidebar groups。
5. 讀取 bounded DOM fields，不保存全頁任意 script／storage／cookie。
6. 擷取 expanded state screenshot；需要時再擷取一張 content pane state。
7. canonicalize manifest、計算 `dom_hash` 與 screenshot sha256。
8. 寫入 JSON／PNG 後呼叫 llmebm local import function或 CLI import；不接受遠端任意回呼 URL。

若 manifest hash 沒變，結束為 `unchanged`，不呼叫 LAVA/RAG。

### 11.2 DynaMed 參考模式

參考模式必須是明確 flag、人工執行、授權 session、固定 domain allowlist。它只產生：

- page／state／action 關係
- headings／ARIA／tabs／accordions／layout regions
- screenshot 用於人工核對
- derived layout rules

完成 llmebm template 對齊後，刪除 raw DynaMed HTML、醫學內容與 screenshots，只保留不含原文的自有規則與驗證紀錄。不得繞過登入、訂閱、robots、CAPTCHA 或網站限制。

## 12. 預計修改檔案與最小責任

### 12.1 llmebm

| 檔案 | 動作 | 責任 |
| --- | --- | --- |
| `llmebm/app/model/sidebar_model.py` | 修改 | 回傳 stable slot metadata；改名不改 slot ID |
| `llmebm/app/model/topic_content_model.py` | 新增 | manifest、content version、job SQLite；原子 current 切換 |
| `llmebm/main_ebm.py` | 修改 | manifest/content/generate/status routes；呼叫 ebm-rag |
| `llmebm/app/templates/specialty.html` | 修改 | accessible title／expand controls、`data-slot-*`、移除 mock RAG HTML |
| `llmebm/app/static/js/topic_content.js` | 新增 | slot click、fetch status/content、白名單 renderer、loading/error state |
| `llmebm/tools/scan_topic_manifest.py` | 新增 | Scrapling DOM＋screenshot scanner、hash、allowlist |
| `llmebm/requirements-scan.txt` | 新增 | scanner-only dependency |
| `llmebm/tests/test_topic_content_contract.py` | 新增 | slot ID、hash、DB version、renderer payload／API contract self-check |

不先重構 `specialty.html` 全部 inline script；只把新 topic content runtime 放入獨立檔，避免本次擴大 diff。

### 12.2 ebm-rag／LAVA

| 檔案 | 動作 | 責任 |
| --- | --- | --- |
| `ebm-rag/lava/adapter/base.py` | 修改 | vision capability contract、safe verification |
| `ebm-rag/lava/adapter/openai_compatible.py` | 修改 | vision message mapping |
| `ebm-rag/lava/adapter/google.py` | 修改 | Gemini inline image mapping |
| `ebm-rag/lava/schemas.py` | 修改 | `vision` verify capability |
| `ebm-rag/lava/api_router.py` | 修改 | vision readiness、兩個 executor |
| `ebm-rag/lava/task_registry.py` | 修改 | `topic_content_plan`、`topic_content_compose` |
| `ebm-rag/lava/matching_tasks/topic_content_plan.py` | 新增 | vision planner、strict plan validator |
| `ebm-rag/lava/matching_tasks/topic_content_compose.py` | 新增 | evidence-only blocks composer、strict validator |
| `ebm-rag/rag_core/core4_ragging/topic_content.py` | 新增 | plan → per-slot retrieve → compose → source gate |
| `ebm-rag/rag_core/core5_api/schemas.py` | 修改 | bounded Topic request schema |
| `ebm-rag/rag_core/core5_api/router.py` | 修改 | `/topic-content/generate` 與 Topic readiness |
| `ebm-rag/tests/test_topic_content_contract.py` | 新增 | task payload、invalid JSON、citation、insufficient evidence、readiness |

## 13. 實作順序與驗收

### Phase A — Slot contract 與 renderer

1. sidebar API 加 stable slot fields。
2. specialty DOM 加 `data-slot-*`，拆開 title selection 與 expand button。
3. 新增白名單 renderer；所有模型文字用 `textContent`／`createTextNode`。
4. 先用固定 fixture content 驗證 UI，不接 LLM。

驗收：

- Universal/custom slots ID 穩定且不撞號。
- custom heading 改名後 slot ID 不變。
- `<script>alert(1)</script>` fixture 只顯示文字，不執行。
- keyboard 可操作 title、expand、tab；focus 狀態可見。
- 未完成 slot 顯示清楚 loading／empty／insufficient／failed 狀態。

### Phase B — Manifest scanner

1. 完成 local origin allowlist。
2. Scrapling 等待 sidebar、展開、取 DOM、截圖、canonical hash。
3. 建立 manifest persistence 與 unchanged short-circuit。

驗收：

- 同一 DOM 連掃兩次 hash 相同，第二次不產生新工作。
- 標題改名或增加 slot 後 hash 改變。
- 純 CSS class／updated date 變更不改 slot hash。
- screenshot 與 DOM 來自相同 browser state。
- 非 allowlisted URL、超大圖片、非 PNG/JPEG 被拒絕。

### Phase C — LAVA vision planner

1. 擴充 vision verify/readiness。
2. 新增 `topic_content_plan` 與 validator。
3. 未綁定時回 `unconfigured`。

驗收：

- vision connection 可驗證、綁定與 invoke。
- chat-only／embedding-only connection 不可錯綁 vision task。
- invalid JSON、未知 slot、未知 block type 被拒絕。
- prompt injection 文字或影像不會改變 required schema／允許 task 執行外部動作。
- 不把 screenshot base64 寫入 log／DB／error。

### Phase D — Topic retrieval 與 composer

1. Topic orchestrator 逐 section 呼叫既有 `retrieve()`。
2. 重用 query strategy 與 strict conditional fallback。
3. 新增 `topic_content_compose`。
4. deterministic source gate 驗證所有 claims／citations。

驗收：

- 每個 citation 都對應 retrieved `paper_id + chunk_id`。
- 無 chunks 時回 `insufficient_evidence`，不生成無來源醫學答案。
- query expansion 失敗時 deterministic retrieval 仍能運行。
- 單一 slot 失敗不影響其他 slot 成功。
- 既有 `/api/v1/rag/query`、`/check` 與 `ebm_generate` 行為未改壞。

### Phase E — llmebm end-to-end

1. generate route 讀 current manifest，呼叫 ebm-rag Topic endpoint。
2. SQLite 保存新版本，驗證成功才切 current。
3. 前端點 slot 顯示 content 與 citations。
4. evidence revision 變更時只標記相關 slot stale。

驗收：

- 新 topic：scan → plan → retrieve → compose → click 顯示完成。
- 一般 reopen／click 不重掃、不重生內容。
- force regenerate 失敗時舊 ready content 仍存在。
- 服務重啟後 running job 轉 interrupted，可安全重跑。
- LAVA／Qdrant 不可用時顯示明確狀態，不顯示 mock 成功。
- 不回傳 provider API key、raw screenshot 或未過 source gate 的內容。

### Phase F — 最小 runnable checks

至少執行：

```powershell
python -m py_compile <所有 touched Python files>
node --check rootmedicals-a/llmebm/app/static/js/topic_content.js
python -m unittest rootmedicals-a/llmebm/tests/test_topic_content_contract.py
python -m unittest rootmedicals-a/ebm-rag/tests/test_topic_content_contract.py
```

再啟動 llmebm、ebm-rag、Qdrant 與已綁定 LAVA，做一個 fixture topic 與一個真實已索引疾病 topic 的瀏覽器驗證。保存：

- manifest JSON 摘要與 hash
- Topic endpoint response 摘要（不含 raw screenshot／secret）
- slot content/citation 對應
- UI screenshots
- skipped checks 與原因

## 14. 硬性安全與品質 gate

1. 第三方頁面與 screenshot 一律視為不可信輸入；其中任何指令都不能改變系統 prompt、schema、URL allowlist 或工具權限。
2. Scanner 不讀 cookies、localStorage、password manager 或瀏覽歷史；授權 session 只由使用者在瀏覽器提供。
3. 任意 URL scan 不暴露為公共 API；防止 SSRF 掃內網 metadata endpoint。
4. llmebm runtime screenshot 不得包含 SOAP、EHR、病人姓名、UID 或其他 PHI；Topic Page scanner 只掃知識頁。
5. LLM 內容不進 `innerHTML`；URL、DOI、citation link 必須由 renderer 驗證 scheme 與格式。
6. RAG strict metadata、chunk quality、source traceability 不得為了填滿頁面而放寬成無來源生成。
7. 新功能未配置時不得改壞既有病例 RAG readiness；Topic endpoint 有自己的 readiness。
8. 失敗重試不得先刪除舊 current content，避免資料遺失。
9. DynaMed 只作版型研究；不保存或展示其受保護內文、圖表、品牌或原始資產。

## 15. 草稿刪除流程

此檔是臨時施工單，不是正式架構文件。

只有在以下條件全部成立後刪除：

1. Phase A-F 驗收全數通過。
2. 使用者明確確認這版實作驗收完成。
3. 在 `rootmedicals-a/doc/` 留下正式 `LLMEBM_DYNAMIC_TOPIC_VERIFICATION_YYYY-MM-DD.md`，包含最終契約、實際修改檔案、驗證結果與剩餘風險。
4. 正式 README／handoff 已指向永久 API 與操作方式，不再引用本草稿。

然後刪除：

```text
rootmedicals-a/_TEMP_LLMEBM_DYNAMIC_TOPIC_IMPLEMENTATION_DRAFT_2026-07-10.md
```

刪除草稿不代表刪除 scanner、schema、tests 或正式驗證報告。

## 16. 已鎖定的設計決定

- DynaMed 只在開發期參考；正式 runtime 掃自己的 llmebm。
- DOM 結構有變才掃；證據有變才重生內容；一般開頁不掃。
- 三個是邏輯角色：vision planner、RAG/query strategy、evidence composer；實體模型可分開綁定。
- LLM 1 必須同時收到 DOM manifest 與至少一張 screenshot；無 vision binding 不假裝成功。
- LLM 3 產生 component JSON，不產生 raw HTML。
- Topic Page 使用新的 application contract，不污染既有病例 `ebm_hits`／traffic-light contract。
- MVP 先完成 Condition Topic Page；Drug、Calculator、Chemo Regimen 等另建 template family，不塞進 condition schema。
