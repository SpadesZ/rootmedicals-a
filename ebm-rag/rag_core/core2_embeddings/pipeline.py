# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core2_embeddings/pipeline.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core2 embedding 層，負責向量化與 embedding provider 呼叫。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core2_embeddings/pipeline.py
# Timestamp: 2026-06-08
# Version: v0.1
# Description: Core2 Embedding Pipeline 入口。
#              從 state DB 讀取 pending chunks，透過 vectorizer.vectorize_paper 執行向量化，
#              並將結果統一回傳給 Core5 API 及 Core3 Indexer 使用。
# ----------------------------------------------------------------------------------------------------

from rag_core.core2_embeddings.vectorizer import vectorize_paper


async def run_embedding_pipeline(paper_id: str) -> dict:
    """
    執行指定 paper_id 的 Embedding Pipeline。

    Returns:
        dict: {status, embedded, failed, model?, provider?, dim?, error?}
    """
    if not paper_id or not isinstance(paper_id, str):
        return {
            "status": "invalid_input",
            "embedded": 0,
            "failed": 0,
            "error": "paper_id must be a non-empty string"
        }

    result = await vectorize_paper(paper_id)
    return result
