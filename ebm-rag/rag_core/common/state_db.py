# 模組定位: ebm-rag 的 SQLite 非同步 state repository。
# 主要責任: 管理 papers/chunks/retrieval logs/pipeline events，並提供 global 與 topic/slot evidence fingerprint。
# 呼叫來源: ingestion、embedding、Qdrant indexing、retrieval、Core5 API readiness 路由。
# 輸入契約: 已正規化的 paper/chunk IDs、JSON metadata、embedding/index 狀態。
# 輸出契約: 可重入持久化狀態、active embedding signatures 與 deterministic scoped revisions。
# 安全邊界: SQL 一律參數化；revision 只雜湊非秘密識別與更新時間，不輸出文獻全文。
# 維護提醒: scope migration 必須可重入；修改 revision fingerprint 欄位即視為 contract 變更。
# ----------------------------------------------------------------------------------------------------

import aiosqlite
import hashlib
import json
import time
from rag_core.common.config import RAG_DB_PATH
from rag_core.common.chunk_quality import MAX_SAFE_CHUNK_TOKENS, evaluate_chunk_quality, summarize_chunk_quality
from rag_core.common.evidence_scope import normalize_evidence_scope, slot_key_from_id

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
    evidence_updated_at REAL,
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
);
CREATE TABLE IF NOT EXISTS chunk_evidence_scopes (
    chunk_id TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    PRIMARY KEY(chunk_id, topic_key, slot_key),
    FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_chunk_evidence_scope
    ON chunk_evidence_scopes(topic_key, slot_key, chunk_id);
CREATE TABLE IF NOT EXISTS evidence_scope_reviews (
    review_id TEXT PRIMARY KEY,
    review_batch_id TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approved_demo', 'rejected', 'needs_revision')),
    reason TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    review_hash TEXT NOT NULL,
    approved_source_ids_json TEXT NOT NULL,
    scope_revision TEXT NOT NULL,
    scope_snapshot_json TEXT NOT NULL,
    scope_aligned_at_record INTEGER NOT NULL CHECK(scope_aligned_at_record IN (0, 1)),
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_scope_review_lookup
    ON evidence_scope_reviews(topic_key, slot_key, recorded_at DESC);
CREATE TRIGGER IF NOT EXISTS evidence_scope_reviews_no_update
BEFORE UPDATE ON evidence_scope_reviews
BEGIN
    SELECT RAISE(ABORT, 'evidence_scope_reviews is append-only');
END;
CREATE TRIGGER IF NOT EXISTS evidence_scope_reviews_no_delete
BEFORE DELETE ON evidence_scope_reviews
BEGIN
    SELECT RAISE(ABORT, 'evidence_scope_reviews is append-only');
END;
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
        if "evidence_updated_at" not in cols:
            await db.execute("ALTER TABLE chunks ADD COLUMN evidence_updated_at REAL")
        # Existing hashes used updated_at; this one-time backfill preserves them until clinical evidence changes.
        await db.execute("UPDATE chunks SET evidence_updated_at=updated_at WHERE evidence_updated_at IS NULL")
        cur = await db.execute("SELECT chunk_id, payload_json FROM chunks")
        for chunk_id, payload_json in await cur.fetchall():
            try:
                payload = json.loads(payload_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            await _replace_chunk_evidence_scopes(db, chunk_id, payload)
        await db.commit()


async def _replace_chunk_evidence_scopes(db, chunk_id: str, payload: dict) -> None:
    topic_key, slot_keys = normalize_evidence_scope(payload)
    await db.execute("DELETE FROM chunk_evidence_scopes WHERE chunk_id=?", (chunk_id,))
    await db.executemany(
        "INSERT INTO chunk_evidence_scopes (chunk_id, topic_key, slot_key) VALUES (?,?,?)",
        [(chunk_id, topic_key, slot_key) for slot_key in slot_keys],
    )


def _scope_revision(topic_key: str, slot_key: str, snapshot: list[dict]) -> str:
    canonical = json.dumps(
        {"topic_key": topic_key, "slot_key": slot_key, "chunks": snapshot},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _get_exact_scope_snapshot(db, topic_key: str, slot_key: str) -> list[dict]:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        """SELECT s.chunk_id, c.paper_id, c.chunk_index, p.metadata_json
             FROM chunk_evidence_scopes s
             JOIN chunks c ON c.chunk_id=s.chunk_id
             LEFT JOIN papers p ON p.paper_id=c.paper_id
            WHERE s.topic_key=? AND s.slot_key=?
            ORDER BY c.paper_id, c.chunk_index, s.chunk_id""",
        (topic_key, slot_key),
    )).fetchall()
    snapshot = []
    for row in rows:
        metadata = _safe_json_object(row["metadata_json"])
        policy = metadata.get("source_policy") if isinstance(metadata.get("source_policy"), dict) else {}
        snapshot.append({
            "chunk_id": str(row["chunk_id"]),
            "paper_id": str(row["paper_id"]),
            "chunk_index": int(row["chunk_index"]),
            "source_version": str(
                policy.get("document_version")
                or metadata.get("document_version")
                or metadata.get("guideline_year")
                or ""
            ),
        })
    return snapshot


def _validate_scope_review_batch(review: dict) -> tuple[str, list[dict]]:
    if not isinstance(review, dict):
        raise ValueError("scope review batch must be an object")
    normalized_topic, _ = normalize_evidence_scope({"topic_key": review.get("topic_key"), "slot_keys": ["*"]})
    if normalized_topic == "*":
        raise ValueError("scope review batch requires a specific topic")
    for key in ("review_batch_id", "mapping_version", "reviewed_by", "reviewed_at", "review_hash"):
        if not str(review.get(key) or "").strip():
            raise ValueError(f"scope review batch requires {key}")
    slots = review.get("slots")
    if not isinstance(slots, list) or not 1 <= len(slots) <= 200:
        raise ValueError("scope review batch requires 1 to 200 slots")
    normalized = []
    seen = set()
    for item in slots:
        if not isinstance(item, dict):
            raise ValueError("scope review slot must be an object")
        slot_key = slot_key_from_id(item.get("slot_key"))
        if slot_key == "*":
            raise ValueError("scope review requires a specific slot")
        if slot_key in seen:
            raise ValueError("scope review contains duplicate slots")
        seen.add(slot_key)
        decision = str(item.get("decision") or "")
        if decision not in {"approved_demo", "rejected", "needs_revision"}:
            raise ValueError(f"invalid scope review decision: {slot_key}")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"scope review requires reason: {slot_key}")
        source_ids = item.get("source_ids")
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or any(not str(source_id or "").strip() or source_id == "*" for source_id in source_ids)
            or len(source_ids) != len(set(source_ids))
        ):
            raise ValueError(f"scope review requires unique nonempty source_ids: {slot_key}")
        normalized.append({
            "slot_key": slot_key,
            "decision": decision,
            "reason": reason,
            "source_ids": sorted(str(source_id) for source_id in source_ids),
        })
    return normalized_topic, normalized


async def record_evidence_scope_review_batch(review: dict) -> dict:
    """Append the reviewer decision together with the exact scope observed at recording time."""
    topic_key, slots = _validate_scope_review_batch(review)
    inserted = 0
    results = []
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        for item in slots:
            slot_key = item["slot_key"]
            snapshot = await _get_exact_scope_snapshot(db, topic_key, slot_key)
            revision = _scope_revision(topic_key, slot_key, snapshot)
            current_sources = sorted({entry["paper_id"] for entry in snapshot})
            aligned = bool(snapshot) and current_sources == item["source_ids"]
            identity = json.dumps(
                [
                    str(review["review_batch_id"]), topic_key, slot_key,
                    str(review["mapping_version"]), item["decision"], item["reason"],
                    str(review["reviewed_by"]), str(review["reviewed_at"]),
                    str(review["review_hash"]), item["source_ids"], revision,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            review_id = "scope-review-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            cur = await db.execute(
                """INSERT OR IGNORE INTO evidence_scope_reviews
                   (review_id, review_batch_id, topic_key, slot_key, mapping_version,
                    decision, reason, reviewed_by, reviewed_at, review_hash,
                    approved_source_ids_json, scope_revision, scope_snapshot_json,
                    scope_aligned_at_record, recorded_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    review_id, str(review["review_batch_id"]), topic_key, slot_key,
                    str(review["mapping_version"]), item["decision"], item["reason"],
                    str(review["reviewed_by"]), str(review["reviewed_at"]), str(review["review_hash"]),
                    json.dumps(item["source_ids"], ensure_ascii=False, separators=(",", ":")),
                    revision, json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    int(aligned), time.time(),
                ),
            )
            inserted += max(0, int(cur.rowcount or 0))
            results.append({
                "slot_key": slot_key,
                "review_id": review_id,
                "scope_revision": revision,
                "approved_source_ids": item["source_ids"],
                "current_source_ids": current_sources,
                "scope_aligned_at_record": aligned,
            })
        await db.commit()
    return {"topic_key": topic_key, "inserted": inserted, "slots": results}


async def get_evidence_scope_review_statuses(topic_key: str, slot_keys: list[str]) -> dict[str, dict]:
    """Return whether the latest append-only review still matches the exact current scope."""
    normalized_topic, _ = normalize_evidence_scope({"topic_key": topic_key, "slot_keys": ["*"]})
    if normalized_topic == "*":
        raise ValueError("scope review status requires a specific topic")
    if not isinstance(slot_keys, list) or not 1 <= len(slot_keys) <= 200:
        raise ValueError("scope review status requires 1 to 200 slots")
    normalized_slots = [slot_key_from_id(slot_key) for slot_key in slot_keys]
    if "*" in normalized_slots or len(set(normalized_slots)) != len(normalized_slots):
        raise ValueError("scope review status requires unique specific slots")

    statuses = {}
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        for slot_key in normalized_slots:
            snapshot = await _get_exact_scope_snapshot(db, normalized_topic, slot_key)
            current_revision = _scope_revision(normalized_topic, slot_key, snapshot)
            current_sources = sorted({entry["paper_id"] for entry in snapshot})
            row = await (await db.execute(
                """SELECT * FROM evidence_scope_reviews
                    WHERE topic_key=? AND slot_key=?
                    ORDER BY recorded_at DESC, rowid DESC LIMIT 1""",
                (normalized_topic, slot_key),
            )).fetchone()
            if not row:
                statuses[slot_key] = {
                    "slot_key": slot_key,
                    "reviewed": False,
                    "current_approved": False,
                    "reason": "no_review",
                    "current_scope_revision": current_revision,
                    "current_source_ids": current_sources,
                }
                continue
            approved_sources = json.loads(row["approved_source_ids_json"])
            if not snapshot:
                reason = "empty_scope"
            elif current_sources != approved_sources:
                reason = "source_set_mismatch"
            elif current_revision != row["scope_revision"]:
                reason = "scope_revision_mismatch"
            elif row["decision"] != "approved_demo":
                reason = "not_approved"
            else:
                reason = "current"
            statuses[slot_key] = {
                "slot_key": slot_key,
                "reviewed": True,
                "review_id": row["review_id"],
                "review_batch_id": row["review_batch_id"],
                "mapping_version": row["mapping_version"],
                "decision": row["decision"],
                "reason": reason,
                "review_reason": row["reason"],
                "reviewed_by": row["reviewed_by"],
                "reviewed_at": row["reviewed_at"],
                "review_hash": row["review_hash"],
                "approved_source_ids": approved_sources,
                "current_source_ids": current_sources,
                "reviewed_scope_revision": row["scope_revision"],
                "current_scope_revision": current_revision,
                "current_approved": reason == "current",
            }
    return statuses

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
    payload = dict(chunk["payload"])
    topic_key, slot_keys = normalize_evidence_scope(payload)
    payload["topic_key"] = topic_key
    payload["slot_keys"] = slot_keys
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, paper_id, chunk_index, text, token_count, title_path_json, payload_json, embedding_status, vector_id, vector_json, indexed_status, qdrant_point_id, created_at, updated_at, evidence_updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chunk["chunk_id"], chunk["paper_id"], chunk["chunk_index"],
                chunk["text"], chunk["token_count"],
                json.dumps(chunk.get("title_path", []), ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                chunk.get("embedding_status", "pending"),
                chunk.get("vector_id"),
                json.dumps(chunk.get("vector"), ensure_ascii=False) if chunk.get("vector") is not None else None,
                chunk.get("indexed_status", "pending"),
                chunk.get("qdrant_point_id"),
                now,
                now,
                now
            )
        )
        await _replace_chunk_evidence_scopes(db, chunk["chunk_id"], payload)
        await db.commit()


async def merge_paper_metadata(paper_id: str, updates: dict) -> bool:
    """Merge reviewed paper metadata without replacing OCR or chunk state."""
    if not isinstance(updates, dict):
        raise ValueError("paper metadata updates must be an object")
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT metadata_json FROM papers WHERE paper_id=?", (paper_id,)
        )).fetchone()
        if not row:
            return False
        metadata = _safe_json_object(row["metadata_json"])
        metadata.update(updates)
        await db.execute(
            "UPDATE papers SET metadata_json=?, updated_at=? WHERE paper_id=?",
            (json.dumps(metadata, ensure_ascii=False), now, paper_id),
        )
        await db.commit()
    return True


async def get_paper_metadata(paper_ids: list[str]) -> dict[str, dict]:
    normalized = list(dict.fromkeys(str(item or "").strip() for item in paper_ids))
    if not normalized or any(not item for item in normalized):
        raise ValueError("paper_ids must be a non-empty list")
    placeholders = ",".join("?" for _ in normalized)
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"SELECT paper_id, metadata_json FROM papers WHERE paper_id IN ({placeholders})",
            normalized,
        )).fetchall()
    return {str(row["paper_id"]): _safe_json_object(row["metadata_json"]) for row in rows}


async def sync_paper_source_policy(paper_id: str, policy: dict) -> int:
    """Persist one reviewed policy and mirror retrieval-critical fields into every chunk payload."""
    if not isinstance(policy, dict):
        raise ValueError("source policy must be an object")
    now = time.time()
    chunk_updates = 0
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        paper = await (await db.execute(
            "SELECT metadata_json FROM papers WHERE paper_id=?", (paper_id,)
        )).fetchone()
        if not paper:
            raise ValueError(f"paper not found: {paper_id}")
        metadata = _safe_json_object(paper["metadata_json"])
        metadata["source_policy"] = policy
        await db.execute(
            "UPDATE papers SET metadata_json=?, updated_at=? WHERE paper_id=?",
            (json.dumps(metadata, ensure_ascii=False), now, paper_id),
        )
        rows = await (await db.execute(
            "SELECT chunk_id, payload_json FROM chunks WHERE paper_id=?", (paper_id,)
        )).fetchall()
        mirrored = {
            "source_lifecycle_status": policy.get("lifecycle_status"),
            "source_license_status": policy.get("license_status"),
            "source_document_version": policy.get("document_version"),
        }
        for row in rows:
            payload = _safe_json_object(row["payload_json"])
            if all(payload.get(key) == value for key, value in mirrored.items()):
                continue
            payload.update(mirrored)
            await db.execute(
                "UPDATE chunks SET payload_json=?, updated_at=? WHERE chunk_id=?",
                (json.dumps(payload, ensure_ascii=False), now, row["chunk_id"]),
            )
            chunk_updates += 1
        await db.commit()
    return chunk_updates


async def update_chunk_evidence_scope(chunk_id: str, topic_key: str, slot_keys: list[str]) -> bool:
    """Update only evidence-scope metadata while preserving the stored vector and index state."""
    now = time.time()
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT payload_json FROM chunks WHERE chunk_id=?", (chunk_id,))
        row = await cur.fetchone()
        if not row:
            return False
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload["topic_key"] = topic_key
        payload["slot_keys"] = slot_keys
        normalized_topic, normalized_slots = normalize_evidence_scope(payload)
        payload["topic_key"] = normalized_topic
        payload["slot_keys"] = normalized_slots
        await db.execute(
            "UPDATE chunks SET payload_json=?, updated_at=? WHERE chunk_id=?",
            (json.dumps(payload, ensure_ascii=False), now, chunk_id),
        )
        await _replace_chunk_evidence_scopes(db, chunk_id, payload)
        await db.commit()
    return True

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
            "UPDATE chunks SET embedding_status=?, vector_id=?, vector_json=?, indexed_status='pending', updated_at=?, evidence_updated_at=? WHERE chunk_id=?",
            (status, vector_id, json.dumps(vector, ensure_ascii=False) if vector is not None else None, now, now, chunk_id)
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
            "UPDATE chunks SET payload_json=?, updated_at=?, evidence_updated_at=? WHERE chunk_id=?",
            (json.dumps(payload, ensure_ascii=False), now, now, chunk_id)
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
    signatures: dict[tuple[str, str, int], dict] = {}
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT chunk_id, paper_id, payload_json, updated_at FROM chunks
               WHERE indexed_status='indexed' ORDER BY chunk_id"""
        )
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
            signature = signatures.setdefault(key, {"count": 0, "hasher": hashlib.sha256()})
            signature["count"] += 1
            fingerprint = json.dumps(
                [str(row["chunk_id"]), str(row["paper_id"]), float(row["updated_at"] or 0)],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            signature["hasher"].update(fingerprint.encode("utf-8") + b"\n")
    return [
        {
            "provider": provider,
            "model": model,
            "dim": dim,
            "indexed_chunk_count": signature["count"],
            "evidence_revision": "sha256:" + signature["hasher"].hexdigest(),
        }
        for (provider, model, dim), signature in sorted(signatures.items())
    ]


async def get_topic_evidence_revisions(topic_key: str, slot_ids: list[str],
                                       *, provider: str = "", model: str = "") -> dict[str, str]:
    normalized_topic, _ = normalize_evidence_scope({"topic_key": topic_key, "slot_keys": ["*"]})
    if normalized_topic == "*":
        raise ValueError("topic_key must identify a specific topic")
    if not isinstance(slot_ids, list) or not 1 <= len(slot_ids) <= 200:
        raise ValueError("slot_ids must contain 1 to 200 items")
    slot_pairs = [(str(slot_id), slot_key_from_id(slot_id)) for slot_id in slot_ids]
    if any(not slot_id or slot_key == "*" for slot_id, slot_key in slot_pairs):
        raise ValueError("slot_ids must contain valid universal/custom slot IDs")
    if len({slot_id for slot_id, _ in slot_pairs}) != len(slot_pairs):
        raise ValueError("slot_ids contains duplicates")

    slot_keys = sorted({slot_key for _, slot_key in slot_pairs})
    placeholders = ",".join("?" for _ in slot_keys)
    query = f"""SELECT c.chunk_id, c.paper_id, c.payload_json, p.metadata_json AS paper_metadata_json,
                        COALESCE(c.evidence_updated_at, c.updated_at) AS evidence_updated_at
                FROM chunks c
                LEFT JOIN papers p ON p.paper_id=c.paper_id
                WHERE c.indexed_status='indexed'
                  AND EXISTS (
                      SELECT 1 FROM chunk_evidence_scopes s
                      WHERE s.chunk_id = c.chunk_id
                        AND s.topic_key IN (?, '*')
                        AND (s.slot_key='*' OR s.slot_key IN ({placeholders}))
                  )
                ORDER BY c.chunk_id"""
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, [normalized_topic, *slot_keys])
        rows = await cur.fetchall()

    fingerprints = {}
    chunks_by_scope = {}
    for row in rows:
        chunk_id = str(row["chunk_id"])
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if provider and payload.get("embedding_provider") != provider:
            continue
        if model and payload.get("embedding_model") != model:
            continue
        fingerprints[chunk_id] = json.dumps(
            [
                chunk_id,
                str(row["paper_id"]),
                float(row["evidence_updated_at"] or 0),
                str(payload.get("embedding_provider") or ""),
                str(payload.get("embedding_model") or ""),
                int(payload.get("embedding_dim") or 0),
                hashlib.sha256(json.dumps(
                    _safe_json_object(row["paper_metadata_json"]).get("source_policy", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")).hexdigest(),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        row_topic, row_slot_keys = normalize_evidence_scope(payload)
        for row_slot_key in row_slot_keys:
            chunks_by_scope.setdefault((row_topic, row_slot_key), set()).add(chunk_id)

    revisions = {}
    for full_slot_id, slot_key in slot_pairs:
        hasher = hashlib.sha256()
        scope_header = json.dumps(
            [normalized_topic, slot_key, str(provider or ""), str(model or "")],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        hasher.update(scope_header.encode("utf-8") + b"\n")
        applicable_chunks = set()
        for scope in (("*", "*"), ("*", slot_key), (normalized_topic, "*"), (normalized_topic, slot_key)):
            applicable_chunks.update(chunks_by_scope.get(scope, ()))
        for chunk_id in sorted(applicable_chunks):
            hasher.update(fingerprints[chunk_id].encode("utf-8") + b"\n")
        revisions[full_slot_id] = "sha256:" + hasher.hexdigest()
    return revisions

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
