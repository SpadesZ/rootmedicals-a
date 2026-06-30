# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core2_embeddings/vectorizer.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core2 embedding 層，負責向量化與 embedding provider 呼叫。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core2_embeddings/vectorizer.py
# Timestamp: 2026-06-08
# Version: v0.2
# Description: 讀取 pending chunks，批次送 embedding，回寫 vector_id 與狀態至 state DB。
#              無 LAVA binding → 回傳 unconfigured；Provider 不支援 → unsupported_provider；向量契約不符時 fail closed。

import uuid

from rag_core.common import state_db as sdb
from rag_core.common.errors import UnconfiguredError, UnsupportedProviderError
from rag_core.core2_embeddings.embedding_client import embed_texts

BATCH_SIZE = 32

async def vectorize_paper(paper_id: str) -> dict:
    pending = await sdb.get_pending_chunks(paper_id)
    if not pending:
        return {"status": "ok", "embedded": 0, "failed": 0, "message": "no pending chunks"}

    embedded_count = 0
    failed_count = 0
    embedding_info = {}

    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        texts = [c["text"] for c in batch]
        try:
            result = await embed_texts(texts)
            vectors = result.get("vectors", [])
            if len(vectors) != len(batch):
                error_message = f"Embedding vector count mismatch: expected {len(batch)}, got {len(vectors)}"
                await sdb.log_event(paper_id, "core2", "vector_count_mismatch", error_message, {"batch_start": i})
                return {
                    "status": "vector_count_mismatch",
                    "error": error_message,
                    "embedded": embedded_count,
                    "failed": len(pending) - embedded_count
                }
            if int(result.get("dim") or 0) <= 0:
                error_message = "Embedding dimension must be positive"
                await sdb.log_event(paper_id, "core2", "embedding_dim_invalid", error_message, {"batch_start": i})
                return {
                    "status": "embedding_dim_invalid",
                    "error": error_message,
                    "embedded": embedded_count,
                    "failed": len(pending) - embedded_count
                }
            embedding_info = {"model": result["model"], "provider": result["provider"], "dim": result["dim"]}
            for chunk, vec in zip(batch, vectors):
                # Use stable id derived from chunk_id so Core2 vector_id == Core3 point_id.
                vid = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk["chunk_id"]))
                await sdb.update_chunk_embedded(chunk["chunk_id"], vid, vector=vec, status="embedded")
                await sdb.update_chunk_embedding_meta(
                    chunk["chunk_id"],
                    provider=result["provider"],
                    model=result["model"],
                    dim=result["dim"]
                )
                embedded_count += 1
        except UnconfiguredError as e:
            await sdb.log_event(paper_id, "core2", "unconfigured", str(e))
            return {"status": "unconfigured", "error": str(e), "embedded": embedded_count, "failed": len(pending) - embedded_count}
        except UnsupportedProviderError as e:
            await sdb.log_event(paper_id, "core2", "unsupported_provider", str(e))
            return {"status": "unsupported_provider", "error": str(e), "embedded": embedded_count, "failed": len(pending) - embedded_count}
        except Exception as e:
            await sdb.log_event(paper_id, "core2", "error", str(e), {"batch_start": i})
            failed_count += len(batch)

    await sdb.log_event(paper_id, "core2", "completed", f"embedded: {embedded_count}, failed: {failed_count}", embedding_info)
    return {"status": "ok", "embedded": embedded_count, "failed": failed_count, **embedding_info}
