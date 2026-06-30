# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/common/state_db.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 共用工具層，集中錯誤、設定、品質與狀態資料庫工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/common/state_db.py
# Timestamp: 2026-06-15
# Version: v0.5
# Description: RAG 系統 SQLite 非同步 ORM（aiosqlite）。
#              管理 papers / ocr_blocks / chunks / retrieval_logs / pipeline_events 五張核心表。
#              init_db() 在 FastAPI lifespan 啟動時呼叫；重複執行不破壞既有資料。
#              提供 readiness gate、literature metadata search 與 embedding signature helper。
# ----------------------------------------------------------------------------------------------------

import aiosqlite
import json
import time
from rag_core.common.config import RAG_DB_PATH
from rag_core.common.chunk_quality import MAX_SAFE_CHUNK_TOKENS, evaluate_chunk_quality, summarize_chunk_quality

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    source_pdf_path TEXT NOT NULL,
    status TEXT NOT NULL,
    total_pages INTEGER DEFAULT 0,
    ocr_raw_json TEXT,
    metadata_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ocr_blocks (
    block_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    page_num INTEGER NOT NULL,
    obj_index INTEGER NOT NULL,
    block_type TEXT,
    text TEXT,
    bbox_json TEXT,
    crop_path TEXT,
    confidence REAL,
    reading_order INTEGER,
    raw_json TEXT,
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    title_path_json TEXT,
    payload_json TEXT NOT NULL,
    embedding_status TEXT DEFAULT 'pending',
    vector_id TEXT,
    vector_json TEXT,
    indexed_status TEXT DEFAULT 'pending',
    qdrant_point_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
);
CREATE TABLE IF NOT EXISTS retrieval_logs (
    query_id TEXT PRIMARY KEY,
    dx_summary TEXT NOT NULL,
    request_json TEXT NOT NULL,
    filters_json TEXT,
    hits_json TEXT,
    ebm_hits_json TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_events (
    event_id TEXT PRIMARY KEY,
    paper_id TEXT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    payload_json TEXT,
    created_at REAL NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.executescript(_INIT_SQL)
        # Backward-compatible migrations for existing local DB files.
        cur = await db.execute("PRAGMA table_info(chunks)")
        cols = {row[1] for row in await cur.fetchall()}
        if "vector_json" not in cols:
            await db.execute("ALTER TABLE chunks ADD COLUMN vector_json TEXT")
        if "indexed_status" not in cols:
            await db.execute("ALTER TABLE chunks ADD COLUMN indexed_status TEXT DEFAULT 'pending'")
        if "qdrant_point_id" not in cols:
            await db.execute("ALTER TABLE chunks ADD COLUMN qdrant_point_id TEXT")
        await db.commit()

async def upsert_paper(paper_id: str, filename: str, pdf_path: str, status: str = "processing", total_pages: int = 0):
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            """INSERT INTO papers (paper_id, filename, source_pdf_path, status, total_pages, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(paper_id) DO UPDATE SET status=excluded.status, total_pages=excluded.total_pages, updated_at=excluded.updated_at""",
            (paper_id, filename, pdf_path, status, total_pages, now, now)
        )
        await db.commit()

async def update_paper_ocr(paper_id: str, raw_data: dict, status: str = "core0_done"):
    now = time.time()
    raw_json = json.dumps(raw_data, ensure_ascii=False)
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            "UPDATE papers SET ocr_raw_json=?, status=?, updated_at=? WHERE paper_id=?",
            (raw_json, status, now, paper_id)
        )
        for blk in raw_data.get("document_stream", []):
            await db.execute(
                """INSERT OR REPLACE INTO ocr_blocks
                   (block_id, paper_id, page_num, obj_index, block_type, text, bbox_json, crop_path, confidence, reading_order, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    blk.get("block_id"), paper_id,
                    blk.get("page_num", 0), blk.get("obj_index", 0),
                    blk.get("type"), blk.get("text"),
                    json.dumps(blk.get("bbox"), ensure_ascii=False),
                    blk.get("source_crop"), blk.get("confidence"),
                    blk.get("obj_index"), json.dumps(blk, ensure_ascii=False)
                )
            )
        await db.commit()

async def upsert_chunk(chunk: dict):
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, paper_id, chunk_index, text, token_count, title_path_json, payload_json, embedding_status, vector_id, vector_json, indexed_status, qdrant_point_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chunk["chunk_id"], chunk["paper_id"], chunk["chunk_index"],
                chunk["text"], chunk["token_count"],
                json.dumps(chunk.get("title_path", []), ensure_ascii=False),
                json.dumps(chunk["payload"], ensure_ascii=False),
                chunk.get("embedding_status", "pending"),
                chunk.get("vector_id"),
                json.dumps(chunk.get("vector"), ensure_ascii=False) if chunk.get("vector") is not None else None,
                chunk.get("indexed_status", "pending"),
                chunk.get("qdrant_point_id"),
                now,
                now
            )
        )
        await db.commit()

async def get_pending_chunks(paper_id: str = None):
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if paper_id:
            cur = await db.execute("SELECT * FROM chunks WHERE embedding_status='pending' AND paper_id=?", (paper_id,))
        else:
            cur = await db.execute("SELECT * FROM chunks WHERE embedding_status='pending'")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

async def update_chunk_embedded(chunk_id: str, vector_id: str, vector: list[float] = None, status: str = "embedded"):
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            "UPDATE chunks SET embedding_status=?, vector_id=?, vector_json=?, indexed_status='pending', updated_at=? WHERE chunk_id=?",
            (status, vector_id, json.dumps(vector, ensure_ascii=False) if vector is not None else None, now, chunk_id)
        )
        await db.commit()

async def update_chunk_embedding_meta(chunk_id: str, provider: str, model: str, dim: int):
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT payload_json FROM chunks WHERE chunk_id=?", (chunk_id,))
        row = await cur.fetchone()
        if not row:
            return
        try:
            payload = json.loads(row[0] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload["embedding_provider"] = provider
        payload["embedding_model"] = model
        payload["embedding_dim"] = dim
        await db.execute(
            "UPDATE chunks SET payload_json=?, updated_at=? WHERE chunk_id=?",
            (json.dumps(payload, ensure_ascii=False), now, chunk_id)
        )
        await db.commit()

async def get_embedded_chunks(paper_id: str):
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM chunks WHERE paper_id=? AND embedding_status='embedded' ORDER BY chunk_index",
            (paper_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

async def mark_chunk_indexed(chunk_id: str, point_id: str):
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            "UPDATE chunks SET indexed_status='indexed', qdrant_point_id=?, updated_at=? WHERE chunk_id=?",
            (point_id, now, chunk_id)
        )
        await db.commit()

async def mark_chunk_quality_blocked(chunk_id: str, issues: list[dict]):
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT payload_json FROM chunks WHERE chunk_id=?", (chunk_id,))
        row = await cur.fetchone()
        if not row:
            return
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload["quality_status"] = "blocked"
        payload["quality_issues"] = issues if isinstance(issues, list) else []
        await db.execute(
            "UPDATE chunks SET indexed_status='quality_blocked', payload_json=?, updated_at=? WHERE chunk_id=?",
            (json.dumps(payload, ensure_ascii=False), now, chunk_id)
        )
        await db.commit()

async def has_indexed_chunks(paper_id: str) -> bool:
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(1) FROM chunks WHERE paper_id=? AND indexed_status='indexed'",
            (paper_id,)
        )
        row = await cur.fetchone()
        return bool(row and row[0] and row[0] > 0)

async def all_chunks_indexed(paper_id: str) -> bool:
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        cur = await db.execute(
            """SELECT
                   COUNT(1) AS total_count,
                   SUM(CASE WHEN indexed_status='indexed' THEN 1 ELSE 0 END) AS indexed_count
               FROM chunks
               WHERE paper_id=?""",
            (paper_id,)
        )
        row = await cur.fetchone()
        if not row:
            return False
        total_count = row[0] or 0
        indexed_count = row[1] or 0
        return total_count > 0 and total_count == indexed_count

async def get_indexed_chunk_count() -> int:
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(1) FROM chunks WHERE indexed_status='indexed'")
        row = await cur.fetchone()
        return int(row[0] or 0) if row else 0

async def get_indexed_embedding_signatures() -> list[dict]:
    signatures: dict[tuple[str, str, int], int] = {}
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT payload_json FROM chunks WHERE indexed_status='indexed'")
        rows = await cur.fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            provider = str(payload.get("embedding_provider") or "")
            model = str(payload.get("embedding_model") or "")
            dim_raw = payload.get("embedding_dim") or 0
            try:
                dim = int(dim_raw)
            except (TypeError, ValueError):
                dim = 0
            if not provider or not model or dim <= 0:
                continue
            key = (provider, model, dim)
            signatures[key] = signatures.get(key, 0) + 1
    return [
        {"provider": provider, "model": model, "dim": dim, "indexed_chunk_count": count}
        for (provider, model, dim), count in sorted(signatures.items())
    ]

def _row_to_quality_chunk(row: dict) -> dict:
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "chunk_id": row.get("chunk_id"),
        "paper_id": row.get("paper_id"),
        "text": row.get("text"),
        "token_count": row.get("token_count"),
        "embedding_status": row.get("embedding_status"),
        "indexed_status": row.get("indexed_status"),
        "payload": payload
    }

async def get_chunk_quality_summary(
    paper_id: str = None,
    indexed_only: bool = False,
    provider: str = None,
    model: str = None,
    max_tokens: int = MAX_SAFE_CHUNK_TOKENS
) -> dict:
    conditions = []
    params = []
    if paper_id:
        conditions.append("paper_id=?")
        params.append(paper_id)
    if indexed_only:
        conditions.append("indexed_status='indexed'")
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM chunks {where_sql} ORDER BY paper_id, chunk_index"

    provider_key = str(provider or "").strip()
    model_key = str(model or "").strip()
    quality_chunks = []
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
        for row in rows:
            item = _row_to_quality_chunk(dict(row))
            payload = item.get("payload") or {}
            if provider_key and payload.get("embedding_provider") != provider_key:
                continue
            if model_key and payload.get("embedding_model") != model_key:
                continue
            quality_chunks.append(item)
    return summarize_chunk_quality(quality_chunks, max_tokens=max_tokens)

async def get_indexed_quality_chunks(provider: str = None, model: str = None) -> list[dict]:
    provider_key = str(provider or "").strip()
    model_key = str(model or "").strip()
    quality_chunks = []
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM chunks WHERE indexed_status='indexed' ORDER BY paper_id, chunk_index")
        rows = await cur.fetchall()
        for row in rows:
            item = _row_to_quality_chunk(dict(row))
            payload = item.get("payload") or {}
            if provider_key and payload.get("embedding_provider") != provider_key:
                continue
            if model_key and payload.get("embedding_model") != model_key:
                continue
            quality_chunks.append(item)
    return quality_chunks

async def quarantine_indexed_quality_failures(provider: str = None, model: str = None) -> dict:
    chunks = await get_indexed_quality_chunks(provider=provider, model=model)
    blocked = 0
    passed = 0
    sample_issues = []
    for chunk in chunks:
        issues = evaluate_chunk_quality(chunk)
        if issues:
            await mark_chunk_quality_blocked(chunk.get("chunk_id"), issues)
            blocked += 1
            if len(sample_issues) < 12:
                sample_issues.extend(issues[:12 - len(sample_issues)])
        else:
            passed += 1
    return {
        "status": "ok",
        "provider": provider,
        "model": model,
        "scanned": len(chunks),
        "passed": passed,
        "quality_blocked": blocked,
        "sample_issues": sample_issues
    }

async def get_indexed_chunk_quality_summary(
    provider: str = None,
    model: str = None,
    max_tokens: int = MAX_SAFE_CHUNK_TOKENS
) -> dict:
    return await get_chunk_quality_summary(
        paper_id=None,
        indexed_only=True,
        provider=provider,
        model=model,
        max_tokens=max_tokens
    )

async def get_indexed_chunk_count_for_embedding(provider: str, model: str) -> int:
    provider_key = str(provider or "")
    model_key = str(model or "")
    if not provider_key or not model_key:
        return 0
    signatures = await get_indexed_embedding_signatures()
    return sum(
        int(signature.get("indexed_chunk_count") or 0)
        for signature in signatures
        if signature.get("provider") == provider_key and signature.get("model") == model_key
    )

async def get_chunks_for_paper(paper_id: str):
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM chunks WHERE paper_id=? ORDER BY chunk_index", (paper_id,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

def _safe_json_object(raw_value) -> dict:
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

def _normal_search_text(value) -> str:
    text = str(value or "").strip().lower()
    normalized = []
    previous_space = False
    for char in text:
        if char.isalnum():
            normalized.append(char)
            previous_space = False
        elif previous_space is False:
            normalized.append(" ")
            previous_space = True
    return " ".join("".join(normalized).split())

def _search_tokens(query: str) -> list[str]:
    return [token for token in _normal_search_text(query).split() if len(token) >= 2]

def _field_score(value, query_text: str, tokens: list[str], weight: int) -> int:
    normalized = _normal_search_text(value)
    if not normalized:
        return 0
    if normalized == query_text:
        return weight * 5
    if query_text and query_text in normalized:
        return weight * 4
    matched = sum(1 for token in tokens if token in normalized)
    if tokens and matched == len(tokens):
        return weight * 3
    if matched > 0 and (len(tokens) <= 2 or matched >= 2):
        return weight * matched
    return 0

def _title_from_candidate(candidate: dict) -> str:
    for key in ("guideline_title", "citation_text", "filename", "paper_id"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    return "Untitled literature"

def _apply_search_field(candidate: dict, field_name: str, value, query_text: str, tokens: list[str], weight: int, chunk_id: str = None) -> None:
    score = _field_score(value, query_text, tokens, weight)
    if score <= 0:
        return
    candidate["score"] += score
    matches = candidate.setdefault("matches", [])
    if field_name not in matches:
        matches.append(field_name)
    if chunk_id:
        matched_chunks = candidate.setdefault("matched_chunks", [])
        if chunk_id not in matched_chunks and len(matched_chunks) < 5:
            matched_chunks.append(chunk_id)

async def search_literature(query: str, limit: int = 20) -> list[dict]:
    safe_query = str(query or "").strip()
    safe_limit = max(1, min(int(limit or 20), 50))
    query_text = _normal_search_text(safe_query)
    tokens = _search_tokens(safe_query)
    if not query_text or not tokens:
        return []

    candidates: dict[str, dict] = {}
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        paper_cur = await db.execute(
            "SELECT paper_id, filename, source_pdf_path, status, metadata_json, total_pages, updated_at FROM papers ORDER BY updated_at DESC"
        )
        paper_rows = await paper_cur.fetchall()
        for paper_row in paper_rows:
            row = dict(paper_row)
            paper_id = row.get("paper_id")
            if not paper_id:
                continue
            metadata = _safe_json_object(row.get("metadata_json"))
            candidate = {
                "paper_id": paper_id,
                "filename": row.get("filename"),
                "source_pdf_path": row.get("source_pdf_path"),
                "status": row.get("status"),
                "total_pages": row.get("total_pages") or 0,
                "updated_at": row.get("updated_at") or 0,
                "guideline_title": metadata.get("guideline_title"),
                "citation_text": metadata.get("citation_text"),
                "doi": metadata.get("doi"),
                "pmid": metadata.get("pmid"),
                "journal": metadata.get("journal"),
                "publication_year": metadata.get("publication_year"),
                "source_type": metadata.get("source_type"),
                "is_guideline": metadata.get("is_guideline"),
                "score": 0,
                "matches": [],
                "matched_chunks": [],
                "chunk_count": 0,
                "indexed_chunk_count": 0
            }
            candidates[paper_id] = candidate
            for field_name, weight in (
                ("paper_id", 9),
                ("filename", 7),
                ("guideline_title", 10),
                ("citation_text", 8),
                ("doi", 10),
                ("pmid", 10),
                ("journal", 4),
                ("source_type", 3)
            ):
                _apply_search_field(candidate, field_name, candidate.get(field_name), query_text, tokens, weight)

        chunk_cur = await db.execute(
            "SELECT chunk_id, paper_id, title_path_json, payload_json, indexed_status FROM chunks ORDER BY paper_id, chunk_index"
        )
        chunk_rows = await chunk_cur.fetchall()
        for chunk_row in chunk_rows:
            row = dict(chunk_row)
            paper_id = row.get("paper_id")
            if paper_id not in candidates:
                continue
            candidate = candidates[paper_id]
            candidate["chunk_count"] += 1
            if row.get("indexed_status") == "indexed":
                candidate["indexed_chunk_count"] += 1
            payload = _safe_json_object(row.get("payload_json"))
            try:
                parsed_title_path = json.loads(row.get("title_path_json") or "[]")
                title_path_value = " > ".join(str(item) for item in parsed_title_path if item)
            except (TypeError, json.JSONDecodeError):
                title_path_value = ""
            for target_key in ("guideline_title", "citation_text", "doi", "pmid", "journal", "publication_year", "source_type", "is_guideline"):
                if candidate.get(target_key) in (None, "", "unknown") and payload.get(target_key) not in (None, "", "unknown"):
                    candidate[target_key] = payload.get(target_key)
            for field_name, value, weight in (
                ("guideline_title", payload.get("guideline_title"), 10),
                ("citation_text", payload.get("citation_text"), 8),
                ("section_title", payload.get("section_title"), 6),
                ("title_path", title_path_value, 6),
                ("doi", payload.get("doi"), 10),
                ("pmid", payload.get("pmid"), 10),
                ("journal", payload.get("journal"), 4),
                ("specialty", payload.get("specialty"), 3),
                ("disease", payload.get("disease"), 4)
            ):
                _apply_search_field(candidate, field_name, value, query_text, tokens, weight, row.get("chunk_id"))

    results = []
    for candidate in candidates.values():
        if candidate["score"] <= 0:
            continue
        candidate["title"] = _title_from_candidate(candidate)
        candidate["matches"] = candidate.get("matches", [])[:8]
        results.append(candidate)
    results.sort(key=lambda item: (-int(item.get("score") or 0), -float(item.get("updated_at") or 0), str(item.get("paper_id") or "")))
    return results[:safe_limit]

async def log_event(paper_id: str, stage: str, status: str, message: str = None, payload: dict = None):
    import uuid
    event_id = str(uuid.uuid4())
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            "INSERT INTO pipeline_events (event_id, paper_id, stage, status, message, payload_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (event_id, paper_id, stage, status, message,
             json.dumps(payload, ensure_ascii=False) if payload else None, now)
        )
        await db.commit()

async def save_retrieval_log(query_id: str, dx_summary: str, request: dict, filters: dict, hits: list, ebm_hits: dict):
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO retrieval_logs
               (query_id, dx_summary, request_json, filters_json, hits_json, ebm_hits_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                query_id, dx_summary,
                json.dumps(request, ensure_ascii=False),
                json.dumps(filters, ensure_ascii=False),
                json.dumps(hits, ensure_ascii=False),
                json.dumps(ebm_hits, ensure_ascii=False),
                now
            )
        )
        await db.commit()

async def get_retrieval_log(query_id: str):
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM retrieval_logs WHERE query_id=?", (query_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
