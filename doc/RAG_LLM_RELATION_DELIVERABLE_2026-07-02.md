

# RootMedicals RAG 與 LLM 關係說明


## 我方目前系統總覽

RootMedicals 現在的主流程可以用一般人的方式理解成：

1. 醫師端或 ClinicalGuard/thin capture client 送出病例資訊。
2. `llmxx-server` 先把 SOAP、ICD、Dx/Tx/Hx 整理成穩定格式。
3. `llmxx-server` 呼叫 `ebm-rag` 的 `/api/v1/rag/check`。
4. `ebm-rag` 把問題轉成適合搜尋的文字。
5. embedding 模型把問題變成向量。
6. Qdrant 向量資料庫找出最接近的醫學文獻 chunks。
7. EBM 生成 LLM 只能根據找回來的 chunks 生成回答。
8. 系統再做來源檢查、ICD gate、禁忌症 gate、claim support 與 final gate。
9. 最後才回到醫師端顯示綠/黃/橘燈與原因。


## 完整 RAG 會用到的 LLM/Embedding 任務

下表是我方目前 RAG 任務拆解。

| 任務 ID | 類型 | 功能 | 是否必要 | 目前實際綁定 |
| --- | --- | --- | --- | --- |
| `semantic_reconstruct` | Chat LLM | PDF/OCR 後的語意重建，將破碎文字整理成可切 chunk 的內容 | 必要 | Google `gemini-2.5-flash` |
| `embedding_dense` | Embedding model | 把文獻 chunk 和查詢文字轉成向量，供 Qdrant 搜尋相似內容 | 必要 | Google `gemini-embedding-001` |
| `ebm_generate` | Chat LLM | 根據 Top-K evidence chunks 生成 EBM JSON、短評、來源與燈號候選 | 必要 | OpenRouter `qwen/qwen3-235b-a22b` |
| `query_decompose` | Chat LLM | 可選地把問題拆成 guideline、efficacy、contraindication、alternatives 等查詢方向 | 可選 | Google `gemini-2.5-flash` |
| `rag_query_strategy` | Chat LLM | 可選地協助醫師問題轉成更好的 RAG 查詢策略 | 可選 | OpenRouter `qwen/qwen3-235b-a22b` |
| `clinical_soap_parse` | Chat LLM | 可選地從 SOAP 輔助抽取 Dx/Tx/Hx；失敗時保留 deterministic fallback | 可選 | Google `gemini-2.5-flash` |
| `llmaaj_adjudicate` | Chat LLM | LLM-as-a-judge，評估 RAG 回答和病例語意是否對齊 | 可選 | OpenRouter `qwen/qwen3-235b-a22b` |
| `claim_verify` | Chat LLM | 檢查治療主張是否真的被 evidence 支持 | 可選 | OpenRouter `qwen/qwen3-235b-a22b` |
| `synthetic_ebm_candidate` | Chat LLM | Demo-only 候選 EBM 產生；仍需 verifier 與來源 gate 通過 | 可選 / Demo | Google `gemini-2.5-flash` |


## 我方 RAG 實際流程

###  文獻進入系統

文獻 PDF 進入後，Core0 先做 OCR、版面處理與語意重建，產出 `document_stream`。

如果文獻是可直接取文字的 PDF，系統也支援 native text fallback，避免 OCR 結果不可用時完全中斷。

###  切 chunk

Core1 將文獻文字切成小段 chunks。

目前 chunk 設計重點：

- 依頁碼與 object order 排序。
- 依標題與章節保留 `title_path`。
- 每段控制在約 300-500 tokens。
- 保留 overlap，避免切段時上下文斷裂。
- 每個 chunk 帶 metadata，例如 `paper_id`、`chunk_id`、`six_s_level`、`ocebm_level`、`specialty`、`disease`、`source_type`、`is_guideline`、禁忌症標記等。

###  embedding

Core2 讀取 pending chunks，呼叫 LAVA 中 `embedding_dense` 綁定的 embedding 模型。

目前是 `gemini-embedding-001`。系統會檢查：

- 回傳向量數量是否等於輸入文字數量。
- 每個向量是否是非空 numeric list。
- 向量維度是否一致。
- provider/model/dim 是否寫回 chunk metadata。

### 寫入 Qdrant

Core3 將已 embedded chunks 寫入 Qdrant。

collection 名稱由 provider、model、dimension 組合而成。這樣可以避免不同 embedding 模型的向量混在一起。

Core3 還會做 chunk quality gate。品質不合格的 chunk 不會被拿去當正式臨床 evidence。

###  醫師問題查詢

當 `llmxx-server` 收到病例後，會組出 RAG check request，內容包含：

- Dx
- Tx
- Hx
- ICD-10 code
- normalized diagnosis
- labs
- top_k

RAG 端會優先使用 ICD 與 normalized diagnosis 作為疾病分類 anchor，避免只靠 OCR 出來的自由文字查錯方向。

###  查詢拆解與向量搜尋

Retriever 預設會用 deterministic 四路查詢：

- guideline / diagnosis criteria
- treatment efficacy
- contraindication / adverse effects
- alternative treatments

如果開啟 LLM-assisted mode，`query_decompose` 或 `rag_query_strategy` 可以提供額外查詢；但這些結果會被嚴格驗證，不合格就回退 deterministic 查詢。

接著系統把查詢文字也送進同一個 embedding model，變成向量，再去 Qdrant 找相近 chunks。

###  EBM 生成

找到 Top-K chunks 後，`ebm_generate` 才會被呼叫。

這個 LLM 被限制只能根據 retrieved chunks 回答，不能自己憑外部知識補文獻、PMID、DOI 或 guideline。

輸出必須是 JSON，並且包含：

- `light_color`
- `llmaaj_score`
- `short_comment`
- `rag_comments`
- `sources`
- `warnings`


## 交付結論

我方目前 RAG 的定位如下：

1. RAG 是 evidence retrieval workflow，不是單一 LLM。
2. Embedding 是 RAG 搜尋的核心，目前使用 Google `gemini-embedding-001`。
3. Qdrant 是向量資料庫，目前 collection 依 embedding signature 建立。
4. Chat LLM 主要負責 evidence-only EBM 生成、查詢輔助、SOAP 輔助解析與 judge/claim 輔助。
5. LAVA 負責 task binding 與 provider 管理。
6. 醫療安全不交給 LLM 單獨決定，而是由 deterministic gates 與 final response builder 控制。
7. 附圖不是目前系統架構圖；它需要補上 embedding、Qdrant、metadata、safety gate，並修正目前模型與 local LLM 的描述。


