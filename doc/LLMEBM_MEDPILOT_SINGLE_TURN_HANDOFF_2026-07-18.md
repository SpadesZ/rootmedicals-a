# 檔案路徑：rootmedicals-a/doc/LLMEBM_MEDPILOT_SINGLE_TURN_HANDOFF_2026-07-18.md
# 產生時間：2026-07-18（Asia/Taipei）
# 版本：v1.0－Medpilot 單輪 EBM Beta 驗收交接
# 模組定位：記錄 llmebm Medpilot 從瀏覽器到既有 RAG／LAVA 的真實問答閉環、驗收證據與未完成邊界。
# 呼叫來源：後續 Codex／工程師／QA／產品驗收者；不由 runtime 載入。
# 輸入／輸出契約：輸入為本次程式、API、Docker、DOM、screenshot 與測試結果；輸出為可重跑的驗收與下一施工順序。
# 安全與維護：不得把 demo draft、未命中 retrieval 的敘述或未過授權 gate 的來源宣稱為正式醫療發布內容；live 數值需重驗後再引用。

## 交付結果

Medpilot 已從假 `setTimeout` 訊息改為可用的單輪 EBM Beta。瀏覽器送出醫療問題後，llmebm 會呼叫既有 `/api/v1/rag/query`，沿用 RAG retrieval、LAVA `ebm_generate` 與 citation validator，再以 Medpilot 公開 contract 回傳。招呼語在本機確定性回覆，不浪費 RAG／LLM；沒有直接證據或 citation 無法追到本次 retrieval chunk 時 fail closed。

## 執行資料流

1. `index.html`／`panels_split.js` 接收單輪問題。
2. `POST /api/v1/medpilot/query` 驗證長度、空白與 null byte。
3. 招呼語直接回傳；臨床問題轉送 `/api/v1/rag/query`。
4. 既有 RAG 檢索 evidence chunks，LAVA 依 evidence 生成結構化段落。
5. `medpilot.py` 只保留可命中本次 retrieved `(paper_id, chunk_id)` 的 section/source。
6. 前端以 `createElement`／`textContent` 顯示 summary、sections、evidence、來源與 retrieval ID，不執行模型 HTML。
7. 無證據、來源不一致、provider/network 失敗皆不以模型常識補答。

## 變更檔案

- `llmebm/app/medpilot.py`：Medpilot 公開 response contract、smalltalk、citation trace 與 fail-closed gate。
- `llmebm/main_ebm.py`：`POST /api/v1/medpilot/query` 與既有 RAG proxy。
- `llmebm/app/templates/index.html`：可存取的 Medpilot dialog、輸入契約與 cache version。
- `llmebm/app/static/js/panels_split.js`：真 API、loading、timeout、offline、Retry、安全 renderer、Enter/Shift+Enter。
- `llmebm/app/static/css/style.css`：回答、citation、錯誤與 disabled 狀態。
- `llmebm/tests/test_medpilot_contract.py`：6 個最小 contract tests。
- `llmebm/tests/test_topic_content_contract.py`：delivery header 與合法 cache-version contract。

## API 契約摘要

Request：

```json
{"query":"What are guideline-supported approaches to stroke prevention in atrial fibrillation?"}
```

Response 必含 `schema=rootmedicals-medpilot-answer.v1`、`status`、`kind`、`summary`、`sections`、`citations`、`query_id` 與 `rag_called`。`status=ok` 的臨床回答必須有可追溯 citation；否則回 `insufficient_evidence`，且 `sections=[]`、`citations=[]`。

## 已執行驗收

- Python syntax：`main_ebm.py`、`app/medpilot.py` PASS。
- JavaScript syntax：Node `--check app/static/js/panels_split.js` PASS。
- 完整 llmebm 測試：`python -m unittest discover -s llmebm/tests -v`，89/89 PASS。
- Docker：`llmebm-standalone` 與 automation service 運行；`/api/health` 正常；RAG ready、178 chunks。
- API：招呼語 `rag_called=false`；空白輸入 422；AF 問題回真 evidence answer；索引外 bacterial meningitis 問題回 `insufficient_evidence` 且 0 citation。
- Browser：滑鼠開啟、Enter 送出、loading、真 AF 回答、來源與 Retrieval ID、輸入恢復、console 0 error 均 PASS。
- Responsive：desktop 與 390×844 widget 無裁切；首頁既有 nav/grid 水平 overflow 為已知、非本包新增問題。

Live receipts：

- HTTP AF query：`85234b12-fb6a-44cf-9ec2-1c9cbc29eac5`。
- HTTP insufficient-evidence query：`aefeb7fb-a35a-4882-8598-afdb2e587879`。
- Browser AF query：`b126542c-b8e4-4812-8269-f91cdbdcf5f5`。

視覺證據：

- `doc/artifacts/llmebm-medpilot-evidence-answer-2026-07-18.png`
- `doc/artifacts/llmebm-medpilot-evidence-answer-top-2026-07-18.png`
- `doc/artifacts/llmebm-medpilot-evidence-summary-2026-07-18.png`
- `doc/artifacts/llmebm-medpilot-mobile-390-2026-07-18.png`

## 未完成／不可誤稱

- 目前是單輪問答；沒有多輪記憶、追問 context 或對話 session。
- 沒有 patient context／PHI workflow，也不應輸入病人識別資料。
- 問答不會直接改疾病頁 slot；疾病頁更新仍走 scan → affected slot → review → regeneration queue。
- 索引主要是 AF evidence（另有 1 個 AR fixture），不是完整醫學知識庫。
- 沒有 streaming response。
- timeout／offline／Retry 已實作與 contract 檢查，但尚未做 browser live fault injection。
- 讀取型問答尚未套用「商用發布」gate；不得把回答直接當作正式發布內容。
- 尚未保存問題 analytics/audit；未定 PHI-safe retention policy 前不應持久化原始問題。
- 390px 首頁既有導覽／專科 grid 水平 overflow 尚待修復。

## 下一施工順序

1. 擴充權威來源 ingestion、版本／撤回／affected-topic metadata 與疾病 golden set。
2. 設計 PHI-safe query audit、保留期限、quota/rate metrics；先不保存原始 PHI。
3. 在逐輪 citation provenance 不丟失的前提下加入 scoped multi-turn。
4. 複用既有 source-detail API，讓 Medpilot citation 可開啟受控 metadata panel。
5. 加 streaming、timeout/offline browser fault injection、首頁 390px overflow 與完整 accessibility matrix。

## 接手者第一個可重跑 Gate

```powershell
cd 'C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a'
python -m unittest discover -s llmebm/tests -v
Invoke-RestMethod 'http://127.0.0.1:33300/api/health'
Invoke-RestMethod 'http://127.0.0.1:33300/api/v1/topic/atrial-fibrillation/content/status'
```

只有測試、health、真 AF citation trace 與索引外 fail-closed 都通過，才可把下一包標成已驗收。
