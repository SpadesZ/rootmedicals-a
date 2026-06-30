# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core1_ingestion/pipeline.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core1 入庫層，負責 chunk、metadata 與語料轉換契約。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core1_ingestion/pipeline.py
# Timestamp: 2026-06-08
# Version: v0.2
# Description: Core1 Ingestion Pipeline 入口。
#              normalize_document_stream → chunk_document → inject_metadata → 寫入 SQLite chunks 表 → 輸出 chunks.json。
# ----------------------------------------------------------------------------------------------------

import json
import os
import asyncio
from rag_core.core1_ingestion.chunker import chunk_document
from rag_core.core1_ingestion.metadata import inject_metadata
from rag_core.common import state_db as sdb
from rag_core.common.config import PROCESS_DIR

_INVALID_TEXT_MARKERS = {
    "[Recognition Error Fallback]",
    "[recognition error fallback]"
}


def _clean_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in _INVALID_TEXT_MARKERS:
        return ""
    return text


def normalize_document_stream(document_stream: list) -> list:
    if not isinstance(document_stream, list):
        return []
    normalized = []
    for index, block in enumerate(document_stream):
        if not isinstance(block, dict):
            continue
        text = _clean_text(block.get("text") or block.get("extracted_content"))
        if not text:
            continue
        page_num = block.get("page_num", block.get("page_number", 1))
        obj_index = block.get("obj_index", block.get("object_id", index))
        block_type = block.get("type", block.get("semantic_type", "Text"))
        normalized.append({
            "block_id": block.get("block_id") or f"page_{page_num}_obj_{obj_index}",
            "page_num": page_num,
            "obj_index": obj_index,
            "type": block_type or "Text",
            "text": text,
            "bbox": block.get("bbox") or block.get("geometry_bbox"),
            "image_url": block.get("image_url"),
            "raw": block
        })
    return normalized


async def run_ingestion(paper_id: str, document_stream: list, filename: str, external_meta: dict = None) -> list:
    normalized_stream = normalize_document_stream(document_stream)
    chunks = chunk_document(paper_id, filename, normalized_stream)
    chunks = [inject_metadata(c, external_meta) for c in chunks]

    for chunk in chunks:
        await sdb.upsert_chunk(chunk)

    # Write chunks.json
    meta_dir = os.path.join(PROCESS_DIR, paper_id, "metadata")
    os.makedirs(meta_dir, exist_ok=True)
    chunks_path = os.path.join(meta_dir, "chunks.json")
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    await sdb.log_event(paper_id, "core1", "completed", f"{len(chunks)} chunks created")
    return chunks
