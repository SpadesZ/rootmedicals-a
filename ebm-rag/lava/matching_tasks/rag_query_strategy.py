# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/rag_query_strategy.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/rag_query_strategy.py
# Timestamp: 2026-06-16
# Version: v0.1
# Description:
#   LAVA task for "physician question to RAG retrieval strategy". It uses the
#   same strict validation as query_decompose but has a dedicated binding so the
#   role is visible and separately testable in LAVA setup.
# ----------------------------------------------------------------------------------------------------

from lava.matching_tasks.query_decompose import _execute_query_decompose_for_task


async def execute_rag_query_strategy(payload: dict) -> dict:
    return await _execute_query_decompose_for_task("rag_query_strategy", payload)
