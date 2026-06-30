# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core3_vector_store/qdrant_client.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core3 向量庫層，負責 Qdrant collection 與索引寫入。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core3_vector_store/qdrant_client.py
# Timestamp: 2026-06-08
# Version: v0.2
# Description: Qdrant 客戶端工廠與健康檢查。health_check() 供 /api/v1/rag/health 呼叫。
# ----------------------------------------------------------------------------------------------------

from rag_core.common.config import QDRANT_TIMEOUT_SECONDS, QDRANT_URL
from qdrant_client import QdrantClient

def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT_SECONDS)

def health_check() -> dict:
    try:
        client = get_qdrant_client()
        info = client.get_collections()
        return {"ok": True, "collections": [c.name for c in info.collections]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
