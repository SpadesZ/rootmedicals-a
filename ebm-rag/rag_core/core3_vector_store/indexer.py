# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core3_vector_store/indexer.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core3 向量庫層，負責 Qdrant collection 與索引寫入。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core3_vector_store/indexer.py
# Timestamp: 2026-06-09
# Version: v0.3
# Description: Core3 Qdrant Indexer。
#              讀取已 embedded chunks 的 vector_json → upsert PointStruct。
#              重跑不產生重複 point（Core2 stable vector_id）；upsert 後先驗證 Qdrant count 再回寫 indexed 狀態。
# ----------------------------------------------------------------------------------------------------

import json
from qdrant_client.http.models import FieldCondition, Filter, MatchValue, PointStruct
from rag_core.common import state_db as sdb
from rag_core.common.chunk_quality import evaluate_chunk_quality
from rag_core.core3_vector_store.collections import collection_signature, ensure_collection, get_client

BATCH_SIZE = 64


def _is_numeric_vector(vector: list) -> bool:
    return (
        isinstance(vector, list)
        and len(vector) > 0
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in vector)
    )


async def index_paper(paper_id: str) -> dict:
    rows = await sdb.get_embedded_chunks(paper_id)

    if not rows:
        return {"status": "no_embedded_chunks", "indexed": 0}

    prepared_rows = []
    signatures = set()
    quality_blocked = 0
    quality_issue_samples = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            vec = json.loads(row.get("vector_json") or "[]")
        except json.JSONDecodeError as e:
            await sdb.log_event(paper_id, "core3", "corrupted_vector_payload", str(e), {"chunk_id": row.get("chunk_id")})
            return {"status": "corrupted_vector_payload", "indexed": 0, "error": str(e)}

        if not _is_numeric_vector(vec):
            await sdb.log_event(paper_id, "core3", "invalid_vector", "vector_json must be a non-empty numeric list", {"chunk_id": row.get("chunk_id")})
            continue

        provider = payload.get("embedding_provider")
        model_id = payload.get("embedding_model")
        dim = payload.get("embedding_dim") or len(vec)
        if not provider or not model_id:
            await sdb.log_event(paper_id, "core3", "missing_embedding_metadata", "embedded chunk lacks provider/model metadata", {"chunk_id": row.get("chunk_id")})
            return {"status": "missing_embedding_metadata", "indexed": 0, "error": "embedded chunk lacks provider/model metadata"}

        if int(dim) != len(vec):
            await sdb.log_event(paper_id, "core3", "embedding_dim_mismatch", "embedding_dim does not match vector_json length", {"chunk_id": row.get("chunk_id"), "embedding_dim": dim, "vector_len": len(vec)})
            return {"status": "embedding_dim_mismatch", "indexed": 0, "error": "embedding_dim does not match vector_json length"}

        payload["text"] = row["text"]
        payload["paper_id"] = payload.get("paper_id") or row.get("paper_id")
        payload["chunk_id"] = row["chunk_id"]
        payload["token_count"] = row.get("token_count")
        quality_issues = evaluate_chunk_quality({
            "chunk_id": row.get("chunk_id"),
            "paper_id": row.get("paper_id"),
            "text": row.get("text"),
            "token_count": row.get("token_count"),
            "payload": payload
        })
        if quality_issues:
            quality_blocked += 1
            if len(quality_issue_samples) < 8:
                quality_issue_samples.extend(quality_issues[:8 - len(quality_issue_samples)])
            await sdb.mark_chunk_quality_blocked(row.get("chunk_id"), quality_issues)
            await sdb.log_event(paper_id, "core3", "quality_blocked", "chunk failed quality gate before Qdrant upsert", {"chunk_id": row.get("chunk_id"), "issues": quality_issues})
            continue
        payload["quality_status"] = "ok"
        payload["quality_issues"] = []

        signatures.add((provider, model_id, len(vec)))
        prepared_rows.append((row, payload, vec))

    if not prepared_rows:
        if quality_blocked > 0:
            return {
                "status": "quality_blocked",
                "indexed": 0,
                "quality_blocked": quality_blocked,
                "quality_issue_samples": quality_issue_samples,
                "error": "all embedded chunks failed quality gate"
            }
        return {"status": "missing_vectors", "indexed": 0, "error": "embedded rows have no vector_json"}

    if len(signatures) > 1:
        signature_list = [
            {"provider": provider, "model": model, "dim": dim}
            for provider, model, dim in sorted(signatures)
        ]
        await sdb.log_event(paper_id, "core3", "mixed_embedding_model", "embedded chunks use multiple embedding providers/models/dimensions", {"signatures": signature_list})
        return {
            "status": "mixed_embedding_model",
            "indexed": 0,
            "error": "embedded chunks use multiple embedding providers/models/dimensions",
            "signatures": signature_list
        }

    provider, model_id, dim = next(iter(signatures))
    signature = collection_signature(provider, model_id, dim)

    col_name = ensure_collection(provider, model_id, dim)
    client = get_client()

    points = []
    for row, payload, vec in prepared_rows:
        point_id = row.get("vector_id")
        if not point_id:
            continue
        points.append(PointStruct(id=point_id, vector=vec, payload=payload))

    if not points:
        return {"status": "missing_vectors", "indexed": 0, "error": "no usable vectors found"}

    # Upsert in batches
    indexed = 0
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i:i + BATCH_SIZE]
        client.upsert(collection_name=col_name, points=batch)
        indexed += len(batch)

    try:
        count_result = client.count(
            collection_name=col_name,
            count_filter=Filter(must=[FieldCondition(key="paper_id", match=MatchValue(value=paper_id))]),
            exact=True
        )
        qdrant_point_count = int(count_result.count or 0)
    except Exception as e:
        await sdb.log_event(paper_id, "core3", "qdrant_count_failed", str(e), {"collection": col_name})
        return {
            "status": "qdrant_count_failed",
            "indexed": 0,
            "error": str(e),
            "collection": col_name,
            "signature": signature
        }

    if qdrant_point_count < indexed:
        error_message = f"Qdrant count verification failed: expected at least {indexed}, got {qdrant_point_count}"
        await sdb.log_event(
            paper_id,
            "core3",
            "qdrant_upsert_verification_failed",
            error_message,
            {"collection": col_name, "qdrant_point_count": qdrant_point_count, "indexed_attempted": indexed}
        )
        return {
            "status": "qdrant_upsert_verification_failed",
            "indexed": 0,
            "error": error_message,
            "collection": col_name,
            "signature": signature,
            "qdrant_point_count": qdrant_point_count
        }

    for p in points:
        chunk_id = p.payload.get("chunk_id")
        if chunk_id:
            await sdb.mark_chunk_indexed(chunk_id, str(p.id))

    await sdb.log_event(paper_id, "core3", "completed", f"indexed {indexed} points to {col_name}",
                        {"collection": col_name, "provider": provider, "model": model_id, "dim": dim, "qdrant_point_count": qdrant_point_count})
    return {
        "status": "ok",
        "indexed": indexed,
        "quality_blocked": quality_blocked,
        "collection": col_name,
        "signature": signature,
        "qdrant_point_count": qdrant_point_count
    }
