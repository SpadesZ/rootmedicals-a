# 模組定位: ebm-rag LAVA task/capability 的單一 allowlist registry。
# 主要責任: 宣告 task id、executor module、required flag 與正確 capability。
# 呼叫來源: LLMModel defaults、LAVA setup UI、readiness 與 invoke router。
# 輸入契約: 靜態、唯一 task definitions；module 必須存在且 capability 可被 adapter 驗證。
# 輸出契約: RAG_TASKS/TASK_IDS，Topic plan/compose 維持 optional readiness。
# 安全邊界: 未登錄 task 不可由 invoke 任意 import；wrong-capability connection 不得 binding。
# 維護提醒: 新 task 先判斷是否真為全域 blocker，Topic optional task 不得拖垮既有 RAG。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/task_registry.py
# Timestamp: 2026-06-16
# Version: v0.5
# Description: RAG 系統 LAVA 任務白名單。取代 FYEDL DEFAULT_TASKS。
#              LLMModel.DEFAULT_TASKS 由此驅動；UI 僅顯示此清單內的任務。
# Change Notes:
#              - v0.4: Added optional llmxx-server Phase 3/4 tasks:
#                clinical_soap_parse and llmaaj_adjudicate. They are not
#                readiness blockers for existing RAG operation.
#              - v0.5: Added rag_query_strategy for physician question to
#                retrieval strategy planning before vector search.
# ----------------------------------------------------------------------------------------------------

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
    },
    {
        "task_id": "query_decompose",
        "label": "Query Decomposition",
        "capability": "chat",
        "module": "lava.matching_tasks.query_decompose",
        "required": False,
        "description": "Core4 可選 LLM 查詢擴充；失敗時回退 deterministic 四路查詢"
    },
    {
        "task_id": "rag_query_strategy",
        "label": "RAG Query Strategy",
        "capability": "chat",
        "module": "lava.matching_tasks.rag_query_strategy",
        "required": False,
        "description": "醫師問題 -> RAG 查詢策略 / 查詢拆解 / 問資料庫前的語意溝通；失敗時回退 deterministic"
    },
    {
        "task_id": "topic_content_plan",
        "label": "Topic Content Planner",
        "capability": "vision",
        "module": "lava.matching_tasks.topic_content_plan",
        "required": False,
        "description": "llmebm Topic manifest + screenshot -> 嚴格 section evidence plan"
    },
    {
        "task_id": "topic_content_compose",
        "label": "Topic Content Composer",
        "capability": "chat",
        "module": "lava.matching_tasks.topic_content_compose",
        "required": False,
        "description": "只依 retrieved evidence 組成白名單 Topic content blocks"
    },
    {
        "task_id": "synthetic_ebm_candidate",
        "label": "Synthetic EBM Candidate",
        "capability": "chat",
        "module": "lava.matching_tasks.synthetic_ebm_candidate",
        "required": False,
        "description": "Demo-only synthetic candidate；只能在 verifier gate 通過後顯示為 evidence-backed"
    },
    {
        "task_id": "claim_verify",
        "label": "Claim Verifier",
        "capability": "chat",
        "module": "lava.matching_tasks.claim_verify",
        "required": False,
        "description": "Demo-only claim-to-evidence 支持度判讀；最終放行仍由 deterministic gate 決定"
    },
    {
        "task_id": "clinical_soap_parse",
        "label": "Clinical SOAP Parse",
        "capability": "chat",
        "module": "lava.matching_tasks.clinical_soap_parse",
        "required": False,
        "description": "llmxx-server 可選 SOAP 語意解析；失敗時回退 deterministic A/P/S/O 映射"
    },
    {
        "task_id": "llmaaj_adjudicate",
        "label": "LLMAAJ Adjudication",
        "capability": "chat",
        "module": "lava.matching_tasks.llmaaj_adjudicate",
        "required": False,
        "description": "llmxx-server 可選語意裁判分數；只能輔助 final gate，不能升級 RAG 安全結果"
    }
]

TASK_IDS = {t["task_id"] for t in RAG_TASKS}
