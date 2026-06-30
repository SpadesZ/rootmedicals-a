# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core2_embeddings/embedding_client.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core2 embedding 層，負責向量化與 embedding provider 呼叫。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core2_embeddings/embedding_client.py
# Timestamp: 2026-06-08
# Version: v0.3
# Description: Core2 Embedding Client。
#              透過 LAVA embedding_dense binding 取得 adapter，批次送出 texts 取得向量。
#              binding 必須連到 embedding-verified active connection；回傳向量需通過數量/維度/型別檢查。
# ----------------------------------------------------------------------------------------------------

from lava.llm_model import LLMModel
from lava.adapter import get_adapter
from rag_core.common.errors import UnconfiguredError, UnsupportedProviderError


def _validate_texts(texts: list[str]) -> list[str]:
    if not isinstance(texts, list):
        raise ValueError("texts must be a list")
    safe_texts: list[str] = []
    for index, text in enumerate(texts):
        if not isinstance(text, str):
            raise ValueError(f"texts[{index}] must be a string")
        safe_texts.append(text)
    return safe_texts


def _validate_vectors(vectors: list, expected_count: int) -> int:
    if not isinstance(vectors, list):
        raise ValueError("Embedding endpoint returned non-list vectors")
    if len(vectors) != expected_count:
        raise ValueError(f"Embedding endpoint returned {len(vectors)} vectors for {expected_count} texts")
    if expected_count == 0:
        return 0

    dim = None
    for vector_index, vector in enumerate(vectors):
        if not isinstance(vector, list) or len(vector) == 0:
            raise ValueError(f"vectors[{vector_index}] must be a non-empty list")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in vector):
            raise ValueError(f"vectors[{vector_index}] contains non-numeric values")
        if dim is None:
            dim = len(vector)
        elif len(vector) != dim:
            raise ValueError(f"vectors[{vector_index}] dimension mismatch: expected {dim}, got {len(vector)}")
    return int(dim or 0)


async def embed_texts(texts: list[str]) -> dict:
    """
    Returns: {"vectors": list[list[float]], "model": str, "provider": str, "dim": int}
    Raises UnconfiguredError or UnsupportedProviderError if not ready.
    """
    safe_texts = _validate_texts(texts)
    conn = LLMModel.get_connection_for_task("embedding_dense")
    if not conn:
        raise UnconfiguredError("embedding_dense has no ready embedding-verified LAVA binding")

    conn = dict(conn)
    adapter = get_adapter(conn["provider"])
    if not adapter:
        raise UnsupportedProviderError(f"Unknown provider: {conn['provider']}")
    if not adapter.supports_embedding:
        raise UnsupportedProviderError(f"Provider {conn['provider']} does not support embedding")

    if not safe_texts:
        return {
            "vectors": [],
            "model": conn["model_id"],
            "provider": conn["provider"],
            "dim": 0
        }

    vectors = await adapter.embed(conn["api_key"], conn["model_id"], safe_texts)
    dim = _validate_vectors(vectors, len(safe_texts))
    return {
        "vectors": vectors,
        "model": conn["model_id"],
        "provider": conn["provider"],
        "dim": dim
    }
