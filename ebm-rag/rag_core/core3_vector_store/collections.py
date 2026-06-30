# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core3_vector_store/collections.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core3 向量庫層，負責 Qdrant collection 與索引寫入。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core3_vector_store/collections.py
# Timestamp: 2026-06-09
# Version: v0.4
# Description: Qdrant Collection 管理。
#              collection name 規則：rootmedicals_ebm_chunks_{provider}_{model_slug}_{dim}。
#              Distance=Cosine；建立 10 個 payload index 供過濾查詢；提供 collection signature 給 readiness gate。
# ----------------------------------------------------------------------------------------------------

import re
from rag_core.common.config import QDRANT_TIMEOUT_SECONDS, QDRANT_URL
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    VectorParams, Distance, PayloadSchemaType
)

_PAYLOAD_INDEXES = [
    ("paper_id", PayloadSchemaType.KEYWORD),
    ("six_s_level", PayloadSchemaType.KEYWORD),
    ("ocebm_level", PayloadSchemaType.KEYWORD),
    ("grade_baseline", PayloadSchemaType.KEYWORD),
    ("specialty", PayloadSchemaType.KEYWORD),
    ("disease", PayloadSchemaType.KEYWORD),
    ("source_type", PayloadSchemaType.KEYWORD),
    ("quality_status", PayloadSchemaType.KEYWORD),
    ("publication_year", PayloadSchemaType.INTEGER),
    ("is_guideline", PayloadSchemaType.BOOL),
    ("has_contraindication_terms", PayloadSchemaType.BOOL),
]


def get_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT_SECONDS)


def collection_name(provider: str, model_id: str, dim: int) -> str:
    safe_dim = int(dim or 0)
    if safe_dim <= 0:
        raise ValueError("collection dimension must be positive")
    provider_slug = re.sub(r"[^a-z0-9]+", "_", str(provider or "").lower()).strip("_") or "provider"
    model_slug = re.sub(r"[^a-z0-9]+", "_", str(model_id or "").lower()).strip("_") or "model"
    return f"rootmedicals_ebm_chunks_{provider_slug}_{model_slug}_{safe_dim}"


def collection_signature(provider: str, model_id: str, dim: int) -> dict:
    safe_dim = int(dim or 0)
    return {
        "provider": str(provider or ""),
        "model": str(model_id or ""),
        "dim": safe_dim,
        "collection": collection_name(provider, model_id, safe_dim)
    }


def ensure_collection(provider: str, model_id: str, dim: int) -> str:
    name = collection_name(provider, model_id, dim)
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
        )
    for field, schema in _PAYLOAD_INDEXES:
        try:
            client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=schema
            )
        except Exception:
            pass
    return name
