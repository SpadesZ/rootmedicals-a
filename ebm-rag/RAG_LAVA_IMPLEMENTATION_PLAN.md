<!--
  檔案路徑: rootmedicals-a/ebm-rag/RAG_LAVA_IMPLEMENTATION_PLAN.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: EBM-RAG 服務入口、Docker 設定、文件或依賴描述。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

# RootMedicals EBM-RAG + LAVA 細項實作規劃

版本日期：2026-06-08

本文件目的：把總設計書與 `Rootmedicals-ebm-rag SAI 系統規格設計說明` 落成可逐步實作的細項計畫。此計畫不可刪減既有規格，只能補齊、優化與明確化。前一版分析偏向 LAVA 控制面板，本版必須同時完成 RAG core1-core5 與 LAVA 的控制/綁定層。

## 0. 已確認現況

依 codegraph 與檔案檢查，現況如下：

- `main_rag.py` 是 FastAPI 應用，已有 `/`、`/admin`、`/api/v1/upload`、`/api/v1/status/{paper_id}`、`/api/v1/history`、`/api/v1/viewer/{paper_id}`、`/api/v1/paper/{paper_id}`。
- `rag_core/core0_literature/lit_pipeline/` 已有五段：`step1_rasterizer.py`、`step2_layouter.py`、`step3_cropper.py`、`step4_recognizer.py`、`step5_reconstructor.py`。
- `step5_reconstructor.py` 目前若找不到 LAVA matching task，會使用 mock `execute_semantic_reconstruction`。後續不可再 silent mock。
- `rag_core/core1_ingestion` 到 `core5_api` 目前是空目錄。
- `lava/llm_model.py` 有可重用 SQLite CRUD，但 `DEFAULT_TASKS` 還是 FYEDL 任務。
- `lava/llm_routes.py` 是 Flask Blueprint，不能直接掛入目前 FastAPI。
- `lava/adapter/` 與 `lava/matching_tasks/` 目前是空目錄。
- `docker-compose-rag.yml` 目前只有 `ebm-rag-engine`，尚未加入 Qdrant。
- `requirements.txt` 目前只有 core0 OCR/layout 需要的依賴，缺 `httpx`、`aiosqlite`、`qdrant-client`、token chunking 工具等。

## 1. 不可變更的上位規格

所有實作都必須保留下列規格，不可降級：

- 系統總架構仍是 `llmxx -> llmebm/ebm-rag -> roothinx`，本階段只做 `ebm-rag` 與 LAVA，不主動重寫 `llmxx`、`llmebm`、`roothinx`。
- `llmxx-server` 未來會傳入 `dx_summary`、Dx、Tx、Hx 或病例摘要，`ebm-rag` 必須回傳 `ebm_hits` JSON。
- RAG 必須支援 6S 金字塔檢索標籤：`System`、`Summaries`、`Synopses`、`Syntheses`、`Studies`。
- RAG 必須支援雙標準證據註解：`ocebm_level` 與 `grade_baseline`。
- LLM 生成內容必須帶證據等級與來源，不可只回傳流暢文字。
- 檢索必須優先上層證據：`System/Summaries/Syntheses` 優先，無精準匹配再退到 `Studies`。
- 需要紅綠燈決策：暗綠燈、暗黃燈、暗橘燈。
- 禁忌症或致死性交互作用命中時，暗橘燈必須覆寫其他結果。
- 醫學計算器類問題不可交給 LLM 自由發揮，必須用本地 Python 函數。
- PDF 解構階段仍使用 PyMuPDF、YOLOv8/DocLayNet、OpenCV、EasyOCR。
- RAG 規格四階段必須完整：Layout/OCR、Semantic Chunking、Vectorization/Qdrant、Retrieval/EBM Generation。

## 2. 實作總架構

實作目標目錄：

```text
ebm-rag/
  main_rag.py
  lava/
    api_router.py
    schemas.py
    task_registry.py
    llm_model.py
    llm_bus.py
    adapter/
      base.py
      openai_compatible.py
      google.py
      anthropic.py
    matching_tasks/
      semantic_reconstruct.py
      ebm_generate.py
  rag_core/
    common/
      config.py
      paths.py
      schemas.py
      state_db.py
      errors.py
    core0_literature/
      lit_pipeline/
      lit_collector.py
    core1_ingestion/
      chunker.py
      metadata.py
      pipeline.py
    core2_embeddings/
      embedding_client.py
      vectorizer.py
      pipeline.py
    core3_vector_store/
      qdrant_client.py
      collections.py
      indexer.py
    core4_ragging/
      retriever.py
      prompts.py
      generator.py
      traffic_light.py
      calculators.py
      pipeline.py
    core5_api/
      router.py
      schemas.py
```

規則：

- 不刪除既有 core0 檔案；只增補或小幅修正。
- 不從 `rootmedicals-faild` 匯入任何 Python 程式，只可參考其 `lava_setup.html` 的互動節奏。
- 不把 FYEDL task 帶入 RAG。`LLMModel.DEFAULT_TASKS` 必須改為 RAG task 註冊表。
- 不再使用 Flask Blueprint 作為新 API。建立 FastAPI `APIRouter`。
- 所有新 API 需掛在 `main_rag.py`：`app.include_router(lava_router)` 與 `app.include_router(rag_router)`。
- 所有工作輸出仍放在 `data/working/process/{paper_id}`，新增 DB/向量狀態放在 `data/sys/database/`。

## 3. 共用資料契約

### 3.1 SQLite 狀態庫

建立 `rag_core/common/state_db.py`，資料庫路徑：

```text
ebm-rag/data/sys/database/rag_state.db
```

使用 `aiosqlite`，至少建立下列表：

```sql
papers(
  paper_id TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  source_pdf_path TEXT NOT NULL,
  status TEXT NOT NULL,
  total_pages INTEGER DEFAULT 0,
  ocr_raw_json TEXT,
  metadata_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
)

ocr_blocks(
  block_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL,
  page_num INTEGER NOT NULL,
  obj_index INTEGER NOT NULL,
  block_type TEXT,
  text TEXT,
  bbox_json TEXT,
  crop_path TEXT,
  confidence REAL,
  reading_order INTEGER,
  raw_json TEXT,
  FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
)

chunks(
  chunk_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  token_count INTEGER NOT NULL,
  title_path_json TEXT,
  payload_json TEXT NOT NULL,
  embedding_status TEXT DEFAULT 'pending',
  vector_id TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
)

retrieval_logs(
  query_id TEXT PRIMARY KEY,
  dx_summary TEXT NOT NULL,
  request_json TEXT NOT NULL,
  filters_json TEXT,
  hits_json TEXT,
  ebm_hits_json TEXT,
  created_at REAL NOT NULL
)

pipeline_events(
  event_id TEXT PRIMARY KEY,
  paper_id TEXT,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  payload_json TEXT,
  created_at REAL NOT NULL
)
```

驗收：

- 啟動 FastAPI 時可初始化 DB。
- 重複初始化不可破壞既有資料。
- JSON 欄位都用 `json.dumps(..., ensure_ascii=False)`。

### 3.2 OCR Raw Node

`lit_collector.collect_unified_raw_data()` 的輸出需保留並可寫入 DB：

```json
{
  "paper_id": "RM_XXXXXXXX",
  "filename": "paper.pdf",
  "total_pages": 12,
  "extraction_status": "success",
  "document_stream": [
    {
      "block_id": "RM_XXXXXXXX:p1:o0",
      "page_num": 1,
      "obj_index": 0,
      "type": "Title|Text|Table|Figure|Caption",
      "text": "...",
      "bbox": [0, 0, 100, 100],
      "confidence": 0.95,
      "image_url": "/outputs/RM_XXXXXXXX/crop/...",
      "source_crop": "..."
    }
  ]
}
```

規則：

- 頁碼維持 1-indexed。
- `block_id` 必須穩定，不可每次重跑變不同。
- 不能用 LLM 猜 `pmid`、`doi`、`ocebm_level`、`grade_baseline`。缺值就填 `null` 或 `unknown`。

### 3.3 Chunk Payload

core1 產出的 chunk 必須符合：

```json
{
  "chunk_id": "RM_XXXXXXXX:chunk:000001",
  "paper_id": "RM_XXXXXXXX",
  "chunk_index": 1,
  "text": "Section title\nchunk text...",
  "token_count": 420,
  "payload": {
    "paper_id": "RM_XXXXXXXX",
    "filename": "paper.pdf",
    "page_start": 1,
    "page_end": 2,
    "block_ids": ["RM_XXXXXXXX:p1:o0"],
    "section_title": "Methods",
    "title_path": ["Abstract", "Methods"],
    "block_types": ["Text"],
    "specialty": "unknown",
    "disease": "unknown",
    "six_s_level": "unknown",
    "ocebm_level": "unknown",
    "grade_baseline": "unknown",
    "source_type": "unknown",
    "publication_year": null,
    "pmid": null,
    "doi": null,
    "journal": null,
    "study_design": "unknown",
    "is_guideline": false,
    "has_contraindication_terms": false
  }
}
```

## 4. LAVA 實作規劃

### 4.1 建立 RAG task registry

新增 `lava/task_registry.py`：

```python
RAG_TASKS = [
    {
        "task_id": "semantic_reconstruct",
        "label": "Semantic Reconstruction",
        "capability": "chat",
        "module": "lava.matching_tasks.semantic_reconstruct",
        "required": True,
        "description": "Core0 OCR 後語意修補與跨區塊重組"
    },
    {
        "task_id": "embedding_dense",
        "label": "Dense Embedding",
        "capability": "embedding",
        "module": "rag_core.core2_embeddings.embedding_client",
        "required": True,
        "description": "Core2 chunk 向量化"
    },
    {
        "task_id": "ebm_generate",
        "label": "EBM Generation",
        "capability": "chat",
        "module": "lava.matching_tasks.ebm_generate",
        "required": True,
        "description": "Core4 Top-K evidence 生成 ebm_hits"
    }
]
```

規則：

- `LLMModel.DEFAULT_TASKS` 必須由 `RAG_TASKS` 產生，不可保留 FYEDL 任務。
- `/api/lava/tasks` 回傳所有 task，並加上 `implemented` 欄位。`implemented` 由 import module 是否成功決定。
- UI 可顯示未實作 task，但不可允許綁定到不存在的 task module。

### 4.2 FastAPI LAVA router

新增 `lava/api_router.py`，不要改用 Flask。路由：

```text
GET    /api/lava/health
GET    /api/lava/connections
POST   /api/lava/connections
PATCH  /api/lava/connections/{connection_id}
DELETE /api/lava/connections/{connection_id}
POST   /api/lava/models/fetch
POST   /api/lava/connections/verify
GET    /api/lava/tasks
GET    /api/lava/bindings
PUT    /api/lava/bindings/{task_id}
POST   /api/lava/tasks/{task_id}/invoke
```

驗收：

- 全部 response 都是 JSON，格式至少有 `ok`。
- API key 不可原文回傳。
- 錯誤不可吞掉，需回傳可定位原因。
- `task_id` 必須驗證在 registry 中。
- 不可再引用 `from flask import ...`。

### 4.3 LAVA adapter 介面

新增 `lava/adapter/base.py`：

```python
class BaseLavaAdapter:
    provider: str
    supports_chat: bool
    supports_embedding: bool

    async def fetch_models(self, api_key: str) -> list[str]: ...
    async def verify_chat(self, api_key: str, model_id: str) -> dict: ...
    async def chat(self, api_key: str, model_id: str, messages: list[dict], temperature: float, max_tokens: int) -> dict: ...
    async def embed(self, api_key: str, model_id: str, texts: list[str]) -> list[list[float]]: ...
```

先做：

- `openai_compatible.py`：支援 OpenAI、xAI、DeepSeek、Mistral、OpenRouter 類 OpenAI compatible endpoint。
- `google.py`：支援 Gemini chat/model list；embedding 若未明確實作，`supports_embedding=False`。
- `anthropic.py`：支援 Anthropic chat/static model list；embedding 為 false。

規則：

- 使用 `httpx.AsyncClient`，不要在 async API 中使用 `requests`。
- Provider endpoint/base URL 要集中在 adapter，不散落在 router。
- embedding 綁定只能選 `supports_embedding=True` 的 connection。

### 4.4 LAVA matching tasks

新增 `lava/matching_tasks/semantic_reconstruct.py`：

- 輸入：OCR full text 或 blocks。
- 呼叫 `semantic_reconstruct` 綁定的 chat model。
- 輸出：

```json
{
  "status": "ok|unconfigured|failed",
  "reconstructed_text": "...",
  "edits": [],
  "model": "...",
  "connection_id": 1,
  "error": null
}
```

新增 `lava/matching_tasks/ebm_generate.py`：

- 輸入：Top-K chunks、dx_summary、case_context。
- 呼叫 `ebm_generate` 綁定的 chat model。
- LLM 只能根據傳入 chunks 生成。
- 輸出需符合 `ebm_hits` schema。

規則：

- 無 LAVA binding 時，不可 mock 成成功；回傳 `status="unconfigured"`。
- 測試可用 fake adapter，但 fake 必須只在 tests 或明確 `ENV=test` 使用。

## 5. RAG core0 實作規劃

目標：保留既有 OCR/layout pipeline，補齊 DB 寫入與 LAVA 語意重組。

### 5.1 保留既有五段

不改變既有輸入/輸出路徑：

```text
data/working/process/{paper_id}/
  {source.pdf}
  png/
  crop/
  recog/
  metadata/
```

### 5.2 修改 step5_reconstructor

`step5_reconstructor.py` 不可再 fallback silent mock。

流程：

1. 讀取 `recog/*-recog.json`。
2. 組出 page text。
3. 呼叫 `lava.matching_tasks.semantic_reconstruct.execute_semantic_reconstruction(...)`。
4. 若 LAVA 未配置，寫入明確狀態：

```json
{
  "page": 1,
  "result": {
    "status": "unconfigured",
    "reconstructed_text": "<raw_text>",
    "error": "semantic_reconstruct has no LAVA binding"
  }
}
```

5. 不讓整個 FastAPI 伺服器崩潰。

驗收：

- 沒有 API key 時，pipeline 可完成 core0，但 status 顯示 semantic reconstruction unconfigured。
- 有 binding 時，`page_x_reconstruction.json` 需包含 model/connection_id。

### 5.3 將 OCR raw 寫入 state DB

在 orchestrator 完成 Step5 後：

1. 呼叫 `collect_unified_raw_data(paper_id, BASE_DIR)`。
2. 寫入 `papers.ocr_raw_json`。
3. 展開寫入 `ocr_blocks`。
4. 寫入 `pipeline_events(stage="core0", status="completed")`。

可新增 `rag_core/core0_literature/persist.py`，避免把 DB 邏輯塞進 orchestrator。

## 6. RAG core1 Semantic Chunking

新增 `rag_core/core1_ingestion/chunker.py`。

輸入：

- `collect_unified_raw_data()` 回傳的 `document_stream`，或 DB `ocr_blocks`。

演算法：

1. 依 `page_num`、`obj_index` 排序。
2. 建立 `current_title_path`。
3. 遇到 `type == "Title"`：
   - 用 regex 判斷層級，例如 `Abstract`、`Introduction`、`Methods`、`Results`、`Discussion`、`Conclusion`、`Diagnosis`、`Treatment`。
   - 更新 `current_title_path`。
   - 不單獨向量化很短標題；要附加到後續 Text。
4. 遇到 `Text`：
   - 將 title path 加在 chunk 開頭。
   - 累積約 300-500 tokens。
   - 超過 500 tokens 時切出 chunk，下一段保留 30-50 tokens overlap。
5. 遇到 `Table/Figure/Caption`：
   - Table/Figure 文字與 Caption 優先形成獨立 chunk。
   - payload 記錄 `block_types` 與 `image_url`。
6. 每個 chunk 產生穩定 `chunk_id`。

Token 規則：

- 優先使用 `tiktoken`。
- 若 `tiktoken` 不可用，用英文空白詞數估算，但 `token_count_method` 要寫進 payload，不可假裝是真 token。

Metadata injection：

- 新增 `rag_core/core1_ingestion/metadata.py`。
- 從使用者輸入、檔案外部 metadata、或明確 PDF 文字抽取。
- 不確定的欄位填 `unknown` 或 `null`。
- 不用 LLM 自行判定 OCEBM 等級，除非後續明確做 evidence-classification task 並留下結果。

輸出：

- `chunks` SQLite 表。
- `data/working/process/{paper_id}/metadata/chunks.json`。

驗收：

- 任一完成 core0 的 paper 可呼叫 core1 產生 chunks。
- chunk schema 符合第 3.3 節。
- 無 chunk 文字不得進入 vectorizer。

## 7. RAG core2 Embeddings

新增：

```text
rag_core/core2_embeddings/embedding_client.py
rag_core/core2_embeddings/vectorizer.py
rag_core/core2_embeddings/pipeline.py
```

流程：

1. 從 SQLite 讀取 `embedding_status='pending'` 的 chunks。
2. 取得 LAVA binding：`task_id='embedding_dense'`。
3. 驗證 connection provider 支援 embedding。
4. 依 batch size 送出 texts。
5. 回寫：
   - `chunks.embedding_status='embedded'`
   - `chunks.vector_id`
   - embedding 維度與模型資訊寫入 payload 或 event。

錯誤規則：

- 無 binding：回傳 `unconfigured`，不產生假 vector。
- provider 不支援 embedding：回傳 `unsupported_provider`。
- API rate limit：依 `rpm_limit`/`tpm_limit` 做節流與 retry。
- 單一 chunk 失敗不得使已成功 chunk 失效；需寫 `pipeline_events`。

驗收：

- 每個 successful chunk 都有 vector。
- 向量維度必須由實際 embedding 回傳決定，不可硬寫。
- 不同 embedding model 不能混在同一 Qdrant collection；若 model/維度不同，需建立或選擇不同 collection name。

## 8. RAG core3 Qdrant Indexing

### 8.1 Docker compose

修改 `docker-compose-rag.yml`，新增：

```yaml
qdrant:
  image: qdrant/qdrant:latest
  container_name: ebm_rag_qdrant
  ports:
    - "6333:6333"
    - "6334:6334"
  volumes:
    - ./data/qdrant/storage:/qdrant/storage
  networks:
    - rag_internal_net
```

`ebm-rag-engine` 增加環境變數：

```yaml
QDRANT_URL: http://qdrant:6333
```

本機非 Docker 開發可用：

```text
QDRANT_URL=http://localhost:6333
```

### 8.2 Collection

新增 `rag_core/core3_vector_store/collections.py`：

- collection name 規則：

```text
rootmedicals_ebm_chunks_{embedding_provider}_{embedding_model_slug}_{vector_size}
```

- distance：Cosine。
- payload indexes：
  - `paper_id`
  - `six_s_level`
  - `ocebm_level`
  - `grade_baseline`
  - `specialty`
  - `disease`
  - `source_type`
  - `publication_year`
  - `is_guideline`
  - `has_contraindication_terms`

### 8.3 Upsert

新增 `indexer.py`：

1. 讀取已 embedded chunks。
2. 建立 Qdrant Point：

```json
{
  "id": "<vector_id>",
  "vector": [0.1, 0.2],
  "payload": {
    "...": "chunk payload",
    "text": "chunk text"
  }
}
```

3. upsert 到 collection。
4. 寫入 event。

驗收：

- Qdrant collection 可被 `/api/v1/rag/health` 檢查。
- 重跑 upsert 不產生重複 point。
- payload filter 可查出指定 `ocebm_level`。

## 9. RAG core4 Retrieval & EBM Generation

新增：

```text
rag_core/core4_ragging/retriever.py
rag_core/core4_ragging/prompts.py
rag_core/core4_ragging/generator.py
rag_core/core4_ragging/traffic_light.py
rag_core/core4_ragging/calculators.py
rag_core/core4_ragging/pipeline.py
```

### 9.1 Query input

API 輸入：

```json
{
  "dx_summary": "Achilles tendinopathy treated with ...",
  "case_context": {
    "dx": "...",
    "tx": "...",
    "hx": "...",
    "age": null,
    "sex": null,
    "labs": {}
  },
  "filters": {
    "specialty": "unknown",
    "disease": "unknown",
    "prefer_six_s_levels": ["System", "Summaries", "Syntheses"],
    "min_ocebm_level": "Level_3"
  },
  "top_k": 10
}
```

### 9.2 Hybrid retrieval

`retriever.py` 必須把單一 `dx_summary` 拆成多個檢索 query：

- Query A：該 Dx 的標準診斷與 gold standard。
- Query B：該 Tx 對該 Dx 的療效。
- Query C：禁忌症、交互作用、安全性。
- Query D：替代方案。

規則：

- 使用 `asyncio.gather` 並發查 Qdrant。
- 初始 filter 優先 `System/Summaries/Syntheses` 與高證據等級。
- 若結果不足，再逐層 fallback 到 `Synopses`、`Studies` 或 `unknown`。
- 最終去重：同一 `paper_id + chunk_id` 只保留最高 score。
- Top-K 預設 10。

### 9.3 EBM prompt

`prompts.py` 需固定系統規則：

- 只能根據提供 chunks 作答。
- 每個臨床建議句尾都要標註 evidence level 與來源。
- 不可編造 PMID/DOI/期刊。
- 缺直接證據時必須寫 `lacking direct evidence`。
- 回傳必須是 JSON，不可 markdown。

### 9.4 ebm_hits schema

`generator.py` 最終回傳：

```json
{
  "query_id": "uuid",
  "light_color": "green|yellow|orange",
  "short_comment": "符合一線實證指引",
  "rag_comments": [
    {
      "topic": "Treatment efficacy",
      "comment": "... (Level_1 evidence, source PMID:123)",
      "evidence_level": "Level_1",
      "grade": "Grade_A",
      "sources": [
        {
          "paper_id": "RM_...",
          "chunk_id": "...",
          "pmid": "123",
          "doi": null,
          "title": null,
          "six_s_level": "Summaries",
          "ocebm_level": "Level_1",
          "score": 0.82
        }
      ]
    }
  ],
  "alternatives": [],
  "warnings": [],
  "retrieval": {
    "queries": [],
    "filters": {},
    "chunks": []
  },
  "model": {
    "provider": "openai",
    "model_id": "configured-model"
  }
}
```

### 9.5 Traffic light rules

`traffic_light.py` 必須先跑 rule-based，再讓 LLM 補充文字。

硬規則：

- `orange`：命中 contraindication、fatal interaction、severe adverse event、或檢索 chunk payload `has_contraindication_terms=true` 且與 Tx 相關。
- `green`：Tx 是一線治療，且支援 evidence 至少有 `ocebm_level=Level_1` 或 `six_s_level in [System, Summaries, Syntheses]`。
- `yellow`：off-label、證據不足、只有 `Level_3` 或 lacking direct evidence。
- orange 具有最高優先權。

### 9.6 Calculators

`calculators.py` 放本地公式，例如：

- CHA2DS2-VASc
- eGFR
- BMI

規則：

- LLM 只能決定是否需要 calculator；實際計算必須本地函數完成。
- calculator input 缺欄位時回傳 `insufficient_data`，不可猜。

驗收：

- 無 LAVA `ebm_generate` binding 時，retrieval 可回 chunks，但 generation 回 `unconfigured`。
- 有 binding 時，必須產出符合 schema 的 JSON。
- 禁忌命中時，即使 LLM 說安全，仍回 orange。

## 10. RAG core5 API

新增 `rag_core/core5_api/router.py` 並在 `main_rag.py` 掛載。

路由：

```text
GET  /api/v1/rag/health
POST /api/v1/rag/index/{paper_id}
GET  /api/v1/rag/papers/{paper_id}/chunks
POST /api/v1/rag/query
POST /api/v1/rag/check
GET  /api/v1/rag/retrieval/{query_id}
```

語意：

- `/health`：檢查 SQLite、Qdrant、LAVA task binding。
- `/index/{paper_id}`：對已完成 core0 的文獻執行 core1-core3。
- `/papers/{paper_id}/chunks`：檢視 chunks 與 payload。
- `/query`：直接接收 `dx_summary` 回 `ebm_hits`。
- `/check`：llmxx-server 未來呼叫的相容入口，輸入 Dx/Tx/Hx，輸出紅綠燈 payload。
- `/retrieval/{query_id}`：查詢歷史 retrieval log。

同時修改既有 `/api/v1/status/{paper_id}`：

新增 steps：

```json
{
  "chunk": true,
  "embed": true,
  "index": true
}
```

不可移除既有：

```json
{
  "rasterize": true,
  "layout": true,
  "crop": true,
  "recognize": true,
  "reconstruct": true
}
```

## 11. 前端 UI 實作規劃

### 11.1 首頁

在 `app/template/index.html` 增加顯眼的 LAVA Setup 入口：

- 可連到 `/lava` 或打開側邊設定面板。
- 不干擾現有 PDF 上傳流程。

### 11.2 LAVA 頁面

新增 `app/template/lava_setup.html` 與 `app/static/js/lava_setup.js`。

互動流程固定：

1. 新增線路。
2. 選 provider。
3. 貼 API key。
4. Fetch Models。
5. 下拉選 model。
6. Test。
7. Connect/Activate。
8. 到 Task Binding 綁定 `semantic_reconstruct`、`embedding_dense`、`ebm_generate`。

規則：

- provider/model/key 都未完成時不可 Connect。
- Fetch Models 失敗要顯示 provider 回傳錯誤。
- Task Binding 只能選已 active 且 capability 相符的 connection。
- `embedding_dense` 不可選不支援 embedding 的 provider。
- 不顯示 FYEDL 任務。

### 11.3 Admin

在 `admin.html` 新增：

- Knowledge Index tab：列出 paper、chunk 數、embedding 狀態、Qdrant indexed 狀態。
- Query Tester tab：輸入 dx_summary，呼叫 `/api/v1/rag/query`，顯示 `ebm_hits`。
- Retrieval Log tab：列出 query_id 與命中 chunks。

## 12. 依賴更新

`requirements.txt` 需新增：

```text
httpx>=0.27.0
aiosqlite>=0.19.0
qdrant-client>=1.9.0
tiktoken>=0.7.0
tenacity>=8.2.0
```

規則：

- 若 `tiktoken` 安裝失敗，chunker 必須有估算 fallback，但 payload 要標明。
- 不要移除既有依賴。

## 13. 實作順序

嚴格照順序做，不要跳到 UI 後才補 core。

### Phase A：共用骨架

1. 新增 `rag_core/common/`。
2. 建立 `state_db.py` 與 schemas。
3. 在 `main_rag.py` startup 或 import 階段初始化 DB。
4. 驗收：`python -m compileall .` 通過。

### Phase B：LAVA FastAPI 化

1. 新增 `lava/task_registry.py`。
2. 修改 `LLMModel.DEFAULT_TASKS` 來源。
3. 新增 `lava/schemas.py`。
4. 新增 `lava/api_router.py`。
5. 在 `main_rag.py` include router。
6. 新增 adapters。
7. 驗收：`GET /api/lava/health`、`GET /api/lava/tasks`、`GET /api/lava/connections` 可用。

### Phase C：Core0 接 DB 與去 mock

1. 新增 core0 persist。
2. 修改 orchestrator 完成後寫入 state DB。
3. 修改 step5 不 silent mock。
4. 驗收：上傳 PDF 後，state DB 有 paper 與 ocr_blocks。

### Phase D：Core1 chunking

1. 實作 chunker。
2. 實作 metadata injection。
3. 寫入 chunks DB 與 `chunks.json`。
4. 驗收：`POST /api/v1/rag/index/{paper_id}` 至少能跑完 chunk 階段。

### Phase E：Core2 embeddings

1. 實作 embedding client。
2. 接 LAVA `embedding_dense` binding。
3. 回寫 vector_id/狀態。
4. 驗收：無 binding 時明確 unconfigured；有 binding 時產生向量。

### Phase F：Core3 Qdrant

1. 修改 docker compose 加 Qdrant。
2. 實作 collection 建立與 payload index。
3. 實作 upsert。
4. 驗收：Qdrant 可查到 points，payload filter 正常。

### Phase G：Core4 retrieval/generation

1. 實作 retriever 多 query 並發。
2. 實作 EBM prompt 與 JSON parser。
3. 實作 traffic light hard rules。
4. 實作 calculators。
5. 驗收：`POST /api/v1/rag/query` 可回 `ebm_hits`。

### Phase H：Core5 API 與 UI

1. 實作 `core5_api/router.py`。
2. 修改 status 增加 chunk/embed/index。
3. 新增 LAVA setup UI。
4. 新增 admin index/query/log。
5. 驗收：完整流程可從 UI 建立 LAVA、上傳 PDF、index、query。

## 14. 防幻覺規則

下一個 AI 實作時必須遵守：

- 不可編造不存在的路由、函式、資料表。先查檔案再改。
- 不可把 `rootmedicals-faild` 的 Flask/Jinja/JS 直接搬進本案後端。
- 不可保留 FYEDL task 白名單。
- 不可 silent mock LLM、embedding、Qdrant。
- 不可自行捏造 PMID、DOI、證據等級、推薦強度。
- 不可因為 provider 沒設定就把 pipeline 標成 success；必須標明 `unconfigured`。
- 不可把不同 embedding 維度寫進同一 Qdrant collection。
- 不可移除既有 core0 五段狀態。
- 不可改掉現有 `paper_id = RM_XXXXXXXX` 命名。
- 不可改掉 1-indexed page。
- 不可讓單筆 query 的 LLM 輸出越過 retrieved chunks 的證據範圍。

## 15. 最小驗收清單

### 無 API key 環境

- FastAPI 可啟動。
- `/api/lava/tasks` 回 RAG tasks。
- `/api/lava/connections` 回空陣列或既有資料。
- 上傳 PDF 可完成 core0。
- Step5 顯示 `unconfigured` 而不是 mock success。
- `/api/v1/rag/index/{paper_id}` 可完成 chunking，但 embeddings 回 `unconfigured`。

### 有 LAVA chat binding

- `semantic_reconstruct` 產出 model/connection_id。
- `ebm_generate` 可依 retrieved chunks 回 JSON。

### 有 LAVA embedding binding + Qdrant

- chunks 可向量化。
- Qdrant collection 建立。
- `/api/v1/rag/query` 回 Top-K chunks 與 ebm_hits。
- 禁忌症命中時強制 orange。

## 16. 本階段刻意不做

- 不重寫 `llmxx-client`。
- 不重寫 `llmxx-server` WebSocket。
- 不重寫 `llmebm` 既有疾病樹。
- 不做 roothinx 論文生成流程。
- 不做自動 evidence classifier，除非另開任務補資料標註來源與人工覆核流程。

