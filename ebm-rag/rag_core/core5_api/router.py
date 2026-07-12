# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core5_api/router.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core5 API 層，對外提供 health/check/admin 等服務端路由。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core5_api/router.py
# Timestamp: 2026-06-17 14:35 +08:00
# Version: v1.0
# Description: Core5 RAG API 路由。
#              掛載至 main_rag.py；提供 /health、/index、/chunks、/query、/check、/retrieval、/literature/search。
#              查詢端 fail-closed；OCR 無有效文字時支援 text-PDF native extraction fallback。
# Change Notes:
#              - v0.8: Let /check accept safe filters so the llmxx closed loop
#                can explicitly enable demo_synthetic_fallback when requested.
#              - v0.9: Carry ICD-10 into /check case_context and dx_summary as
#                a disease-classification anchor.
#              - v1.0: Derive narrow retrieval metadata filters from supported
#                ICD families so closed-loop checks do not mix unrelated
#                disease chunks before final gating.
# ----------------------------------------------------------------------------------------------------

import hmac
import json
import os
import aiosqlite

from fastapi import APIRouter, Body, HTTPException, Query, Request

from rag_core.core5_api.schemas import QueryRequest, CheckRequest, TopicContentGenerateRequest
from rag_core.common import state_db as sdb
from rag_core.common.config import RAG_DB_PATH
from rag_core.core3_vector_store.collections import collection_name
from rag_core.core3_vector_store.qdrant_client import health_check as qdrant_health
from rag_core.core1_ingestion.pipeline import run_ingestion
from rag_core.core2_embeddings.pipeline import run_embedding_pipeline
from rag_core.core3_vector_store.indexer import index_paper as qdrant_index
from rag_core.core4_ragging.pipeline import run_query
from rag_core.core4_ragging.topic_content import generate_topic_content
from rag_core.common.chunk_quality import evaluate_chunk_quality
from lava.task_registry import RAG_TASKS
from lava.llm_model import LLMModel
from lava.api_router import _task_readiness

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])

_INVALID_TEXT_MARKERS = {
    "[Recognition Error Fallback]",
    "[recognition error fallback]"
}

_ALLOWED_EXTERNAL_META_KEYS = {
    "specialty",
    "disease",
    "six_s_level",
    "ocebm_level",
    "grade_baseline",
    "source_type",
    "journal",
    "study_design",
    "is_guideline",
    "pmid",
    "doi",
    "publication_year",
    "guideline_title",
    "guideline_organization",
    "guideline_year",
    "source_url",
    "guideline_url",
    "citation_text"
}


def _clean_stream_text(block: dict) -> str:
    if not isinstance(block, dict):
        return ""
    text = str(block.get("text") or block.get("extracted_content") or "").strip()
    if text in _INVALID_TEXT_MARKERS:
        return ""
    return text


def _has_usable_document_text(document_stream: list) -> bool:
    if not isinstance(document_stream, list):
        return False
    return any(bool(_clean_stream_text(block)) for block in document_stream)


def _external_meta_from_body(body: dict | None) -> dict | None:
    if body is None:
        return None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="index request body must be a JSON object")
    raw_meta = body.get("external_meta", body.get("metadata"))
    if raw_meta is None:
        return None
    if not isinstance(raw_meta, dict):
        raise HTTPException(status_code=400, detail="external_meta must be a JSON object")
    safe_meta = {}
    for key, value in raw_meta.items():
        if key in _ALLOWED_EXTERNAL_META_KEYS and value not in (None, "", "unknown"):
            safe_meta[key] = value
    return safe_meta or None


def _default_filters_from_icd(icd10_code: str, normalized_diagnosis: str) -> dict:
    code = str(icd10_code or "").strip().upper()
    diagnosis = str(normalized_diagnosis or "").strip().lower()
    filters = {
        "source_type": "guideline",
        "is_guideline": True
    }
    if code.startswith("J30") or "allergic rhinitis" in diagnosis:
        filters.update({
            "specialty": "allergy_immunology",
            "disease": "allergic_rhinitis"
        })
    elif code.startswith("I48") or "atrial fibrillation" in diagnosis:
        filters.update({
            "specialty": "cardiology",
            "disease": "atrial_fibrillation"
        })
    return filters


def _merge_check_filters(user_filters: dict | None, icd10_code: str, normalized_diagnosis: str) -> dict:
    merged = _default_filters_from_icd(icd10_code, normalized_diagnosis)
    for key, value in (user_filters or {}).items():
        if value not in (None, "", "unknown"):
            merged[key] = value
    return merged


def _extract_pdf_text_stream(pdf_path: str) -> list:
    if not pdf_path:
        return []
    try:
        import fitz
    except Exception:
        return []
    blocks = []
    try:
        with fitz.open(pdf_path) as doc:
            for page_index, page in enumerate(doc, start=1):
                text = str(page.get_text("text") or "").strip()
                if not text:
                    continue
                blocks.append({
                    "block_id": f"pdf_text_page_{page_index}",
                    "page_num": page_index,
                    "obj_index": 0,
                    "type": "Text",
                    "text": text,
                    "source": "pdf_native_text"
                })
    except Exception:
        return []
    return blocks


async def _rag_readiness(require_indexed_chunks: bool = True) -> dict:
    import os
    db_ok = os.path.exists(RAG_DB_PATH)
    qdrant = qdrant_health()
    lava = _task_readiness()
    indexed_chunk_count = await sdb.get_indexed_chunk_count()
    embedding_signatures = await sdb.get_indexed_embedding_signatures()
    embedding_conn = LLMModel.get_connection_for_task("embedding_dense")
    active_embedding_index = {
        "provider": None,
        "model": None,
        "indexed_chunk_count": 0,
        "signatures": [],
        "collections": [],
        "quality": None
    }
    reasons = []

    if embedding_conn:
        embedding_conn = dict(embedding_conn)
        active_provider = embedding_conn.get("provider")
        active_model = embedding_conn.get("model_id")
        active_signatures = [
            signature for signature in embedding_signatures
            if signature.get("provider") == active_provider and signature.get("model") == active_model
        ]
        active_collections = []
        for signature in active_signatures:
            try:
                active_collections.append(collection_name(active_provider, active_model, int(signature.get("dim") or 0)))
            except (TypeError, ValueError):
                continue
        active_embedding_index = {
            "provider": active_provider,
            "model": active_model,
            "indexed_chunk_count": sum(int(signature.get("indexed_chunk_count") or 0) for signature in active_signatures),
            "signatures": active_signatures,
            "collections": active_collections,
            "quality": await sdb.get_indexed_chunk_quality_summary(active_provider, active_model)
        }

    if not db_ok:
        reasons.append("sqlite_state_db_missing")
    if not qdrant.get("ok", False):
        reasons.append("qdrant_unavailable")
    if not lava.get("ready", False):
        reasons.append("lava_tasks_not_ready")
    if require_indexed_chunks and active_embedding_index["indexed_chunk_count"] < 1:
        reasons.append("no_indexed_chunks_for_active_embedding")
    if require_indexed_chunks and active_embedding_index["indexed_chunk_count"] > 0 and qdrant.get("ok", False):
        qdrant_collections = set(qdrant.get("collections") or [])
        active_collections = set(active_embedding_index.get("collections") or [])
        if active_collections and not active_collections.intersection(qdrant_collections):
            reasons.append("active_embedding_collection_missing_in_qdrant")
    if require_indexed_chunks and active_embedding_index["indexed_chunk_count"] > 0:
        quality = active_embedding_index.get("quality") or {}
        if quality.get("query_safe") is not True:
            reasons.append("indexed_chunk_quality_gate_failed")

    return {
        "ready": len(reasons) == 0,
        "reasons": reasons,
        "sqlite": db_ok,
        "qdrant": qdrant,
        "lava": lava,
        "indexed_chunk_count": indexed_chunk_count,
        "embedding_signatures": embedding_signatures,
        "active_embedding_index": active_embedding_index,
        "requires_indexed_chunks": require_indexed_chunks
    }


def _task_ready(readiness: dict, task_id: str) -> bool:
    task = readiness.get("lava", {}).get("tasks", {}).get(task_id, {})
    return bool(task.get("ready"))


def _raise_not_ready(readiness: dict):
    raise HTTPException(
        status_code=409,
        detail={
            "status": "not_ready",
            "message": "RAG retrieval is not ready. Clinical evidence retrieval is blocked until all readiness gates pass.",
            "readiness": readiness
        }
    )


async def _topic_readiness() -> dict:
    base = await _rag_readiness(require_indexed_chunks=True)
    required_task_ids = ("topic_content_plan", "embedding_dense", "topic_content_compose")
    required_tasks = {}
    reasons = []
    for task_id in required_task_ids:
        task = dict(base.get("lava", {}).get("tasks", {}).get(task_id, {}))
        required_tasks[task_id] = {
            "ready": bool(task.get("ready")),
            "reason": task.get("reason"),
            "capability": task.get("capability"),
            "connection_id": task.get("connection_id"),
        }
        if not task.get("ready"):
            reasons.append(f"{task_id}:{task.get('reason') or 'not_ready'}")

    infrastructure_reasons = {
        "sqlite_state_db_missing",
        "qdrant_unavailable",
        "no_indexed_chunks_for_active_embedding",
        "active_embedding_collection_missing_in_qdrant",
        "indexed_chunk_quality_gate_failed",
    }
    reasons.extend(reason for reason in base.get("reasons", []) if reason in infrastructure_reasons)
    reasons = list(dict.fromkeys(reasons))
    return {
        "ready": not reasons,
        "reasons": reasons,
        "required_tasks": required_tasks,
        "sqlite": base.get("sqlite"),
        "qdrant": base.get("qdrant"),
        "active_embedding_index": base.get("active_embedding_index"),
    }


def _raise_topic_not_ready(readiness: dict) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "status": "not_ready",
            "message": "Topic content generation requires ready vision planning, embedding retrieval, and composition tasks.",
            "readiness": readiness,
        },
    )


def _authorize_topic_content_request(request: Request) -> None:
    configured_token = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "")
    if configured_token:
        supplied_token = request.headers.get("X-LLMEBM-Topic-Token", "")
        if supplied_token and hmac.compare_digest(supplied_token, configured_token):
            return
        raise HTTPException(status_code=401, detail="Invalid or missing topic generation token")
    client_host = str(getattr(getattr(request, "client", None), "host", "") or "").strip().lower()
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(
            status_code=403,
            detail="Topic generation is loopback-only unless LLMEBM_TOPIC_GENERATION_TOKEN is configured",
        )


# ──────────────────────────────────────────────
# GET /api/v1/rag/health
# ──────────────────────────────────────────────

@router.get("/health")
async def rag_health():
    """
    系統健康檢查：SQLite、Qdrant、LAVA task binding 三合一。
    """
    readiness = await _rag_readiness(require_indexed_chunks=True)
    return {
        "ok": readiness["ready"],
        "status": "ready" if readiness["ready"] else "not_ready",
        "readiness": readiness
    }


@router.get("/topic-content/readiness")
async def topic_content_readiness_endpoint():
    return await _topic_readiness()


@router.post("/topic-content/generate")
async def generate_topic_content_endpoint(body: TopicContentGenerateRequest, request: Request):
    from lava.matching_tasks.topic_content_plan import validate_topic_manifest

    _authorize_topic_content_request(request)
    try:
        manifest = validate_topic_manifest(body.manifest)
        target_ids = {slot["slot_id"] for slot in manifest["slots"] if slot["content_target"]}
        unknown = set(body.only_slot_ids).difference(target_ids)
        if unknown:
            raise ValueError(f"only_slot_ids contains unknown or non-content slots: {', '.join(sorted(unknown))}")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    readiness = await _topic_readiness()
    if not readiness["ready"]:
        _raise_topic_not_ready(readiness)
    return await generate_topic_content(
        manifest=manifest,
        screenshots=[item.model_dump() for item in body.screenshots],
        only_slot_ids=body.only_slot_ids,
        filters=body.filters,
        top_k=body.top_k,
    )


# ──────────────────────────────────────────────
# POST /api/v1/rag/index/{paper_id}
# ──────────────────────────────────────────────

@router.post("/index/{paper_id}")
async def index_paper_endpoint(paper_id: str, body: dict | None = Body(default=None)):
    """
    對已完成 Core0 的文獻執行 Core1（Chunk）→ Core2（Embed）→ Core3（Qdrant Upsert）。
    前置條件：papers 表中必須有對應紀錄且 ocr_raw_json 不為 null。
    """
    if not paper_id:
        raise HTTPException(status_code=400, detail="paper_id is required")

    # 1. 讀取 paper 紀錄
    async with aiosqlite.connect(RAG_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,))
        paper_row = await cur.fetchone()

    if not paper_row:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found in DB. Run upload first.")

    paper = dict(paper_row)
    raw_json_str = paper.get("ocr_raw_json")
    if not raw_json_str:
        raise HTTPException(
            status_code=400,
            detail="Core0 OCR data not found. Ensure pipeline has completed Step 1-5."
        )

    # 2. 解析 OCR raw data
    try:
        raw_data = json.loads(raw_json_str)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"OCR raw JSON is corrupted: {e}")

    document_stream = raw_data.get("document_stream", [])
    if not document_stream:
        raise HTTPException(status_code=400, detail="document_stream is empty. Core0 output may be incomplete.")
    ingestion_source = "core0_document_stream"
    if not _has_usable_document_text(document_stream):
        fallback_stream = _extract_pdf_text_stream(paper.get("source_pdf_path"))
        if fallback_stream:
            document_stream = fallback_stream
            ingestion_source = "pdf_native_text_fallback"
        else:
            raise HTTPException(
                status_code=422,
                detail="Core0 document_stream contains no usable text and PDF native text fallback is unavailable"
            )

    external_meta = _external_meta_from_body(body)

    # 3. Core1 — Chunking
    filename = paper.get("filename", "unknown.pdf")
    chunks = await run_ingestion(paper_id, document_stream, filename, external_meta=external_meta)

    readiness_before_embedding = await _rag_readiness(require_indexed_chunks=False)
    if not _task_ready(readiness_before_embedding, "embedding_dense"):
        return {
            "ok": False,
            "status": "partial_chunked_not_indexed",
            "paper_id": paper_id,
            "ready_for_query": False,
            "chunks_created": len(chunks),
            "ingestion_source": ingestion_source,
            "embed": {
                "status": "not_ready",
                "error": "embedding_dense task is not ready"
            },
            "index": {
                "status": "skipped",
                "indexed": 0,
                "reason": "embedding task not ready"
            },
            "readiness": readiness_before_embedding
        }

    # 4. Core2 — Embedding
    embed_result = await run_embedding_pipeline(paper_id)

    # 5. Core3 — Qdrant Upsert（僅在 embedding 成功時執行）
    if embed_result.get("status") == "ok" and embed_result.get("embedded", 0) > 0:
        qdrant = qdrant_health()
        if not qdrant.get("ok", False):
            index_result = {
                "status": "qdrant_unavailable",
                "indexed": 0,
                "error": qdrant.get("error", "Qdrant health check failed")
            }
        else:
            try:
                index_result = await qdrant_index(paper_id)
            except Exception as e:
                index_result = {
                    "status": "index_failed",
                    "indexed": 0,
                    "error": str(e)
                }
    else:
        index_result = {
            "status": embed_result.get("status", "skipped"),
            "indexed": 0,
            "reason": "embedding not completed"
        }

    ready_for_query = embed_result.get("status") == "ok" and index_result.get("status") == "ok" and index_result.get("indexed", 0) > 0

    return {
        "ok": ready_for_query,
        "status": "indexed_ready" if ready_for_query else "indexed_not_ready",
        "paper_id": paper_id,
        "ready_for_query": ready_for_query,
        "chunks_created": len(chunks),
        "ingestion_source": ingestion_source,
        "external_meta_applied": external_meta or {},
        "embed": embed_result,
        "index": index_result
    }


# ──────────────────────────────────────────────
# POST /api/v1/rag/quality/quarantine
# ──────────────────────────────────────────────

@router.post("/quality/quarantine")
async def quarantine_indexed_quality_failures_endpoint():
    """
    非破壞性隔離已 indexed 但不符合品質 gate 的 chunks。
    不刪除 paper/OCR/chunk 原文；只把 indexed_status 改為 quality_blocked。
    Retriever 另有 quality_status='ok' filter，可避免舊 Qdrant points 被撈回。
    """
    embedding_conn = LLMModel.get_connection_for_task("embedding_dense")
    if not embedding_conn:
        raise HTTPException(status_code=409, detail="embedding_dense task is not bound")
    embedding_conn = dict(embedding_conn)
    result = await sdb.quarantine_indexed_quality_failures(
        provider=embedding_conn.get("provider"),
        model=embedding_conn.get("model_id")
    )
    result["readiness"] = await _rag_readiness(require_indexed_chunks=True)
    return result


# ──────────────────────────────────────────────
# GET /api/v1/rag/literature/search
# ──────────────────────────────────────────────

@router.get("/literature/search")
async def search_literature_endpoint(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=12, ge=1, le=50)
):
    """
    搜尋已匯入文獻的可追溯 metadata：標題、檔名、paper_id、DOI、PMID、期刊與 chunk 標題路徑。
    """
    query = str(q or "").strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Search query must be at least 2 characters")

    results = await sdb.search_literature(query, limit=limit)
    return {
        "ok": True,
        "query": query,
        "total": len(results),
        "results": results
    }


# ──────────────────────────────────────────────
# GET /api/v1/rag/papers/{paper_id}/chunks
# ──────────────────────────────────────────────

@router.get("/papers/{paper_id}/chunks")
async def get_chunks_endpoint(paper_id: str):
    """
    列出指定 paper 的所有 chunks 與 payload。
    """
    if not paper_id:
        raise HTTPException(status_code=400, detail="paper_id is required")

    chunks = await sdb.get_chunks_for_paper(paper_id)
    if not chunks:
        raise HTTPException(status_code=404, detail=f"No chunks found for paper '{paper_id}'")

    result = []
    for c in chunks:
        try:
            payload = json.loads(c.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        chunk_payload = {
            "chunk_id": c.get("chunk_id"),
            "paper_id": c.get("paper_id"),
            "chunk_index": c.get("chunk_index"),
            "text": c.get("text"),
            "token_count": c.get("token_count"),
            "embedding_status": c.get("embedding_status"),
            "indexed_status": c.get("indexed_status"),
            "vector_id": c.get("vector_id"),
            "payload": payload
        }
        quality_issues = evaluate_chunk_quality(chunk_payload)
        chunk_payload["quality_status"] = "ok" if not quality_issues else "blocked"
        chunk_payload["quality_issues"] = quality_issues
        result.append(chunk_payload)

    return {
        "paper_id": paper_id,
        "total": len(result),
        "quality": await sdb.get_chunk_quality_summary(paper_id=paper_id),
        "chunks": result
    }


# ──────────────────────────────────────────────
# POST /api/v1/rag/query
# ──────────────────────────────────────────────

@router.post("/query")
async def query_rag_endpoint(body: QueryRequest):
    """
    直接接收 dx_summary，回傳 ebm_hits（含紅綠燈、RAG comments、來源標注）。
    """
    if not body.dx_summary or not body.dx_summary.strip():
        raise HTTPException(status_code=400, detail="dx_summary is required and must not be empty")

    readiness = await _rag_readiness(require_indexed_chunks=True)
    if not readiness["ready"]:
        _raise_not_ready(readiness)

    result = await run_query(
        dx_summary=body.dx_summary,
        case_context=body.case_context or {},
        filters=body.filters or {},
        top_k=max(1, min(body.top_k, 50))
    )
    return result


# ──────────────────────────────────────────────
# POST /api/v1/rag/check
# ──────────────────────────────────────────────

@router.post("/check")
async def check_ebm_endpoint(body: CheckRequest):
    """
    llmxx-server 相容入口。輸入 Dx/Tx/Hx，輸出紅綠燈 payload（ebm_hits schema）。
    """
    if not body.dx or not body.dx.strip():
        raise HTTPException(status_code=400, detail="dx is required")
    if body.filters is not None and not isinstance(body.filters, dict):
        raise HTTPException(status_code=400, detail="filters must be an object")

    readiness = await _rag_readiness(require_indexed_chunks=True)
    if not readiness["ready"]:
        _raise_not_ready(readiness)

    icd10_code = body.icd10_code or body.icd_code or ""
    diagnosis_label = str(body.diagnosis_label or body.dx or "").strip()
    normalized_diagnosis = str(body.normalized_diagnosis or body.dx or diagnosis_label).strip()
    dx_text = str(body.dx_text or body.dx or "").strip()

    # 組合 dx_summary 供 retriever 使用。ICD code + normalized diagnosis
    # 放在最前面，A 欄 OCR/醫師原文只作為 assessment context。
    parts = []
    if icd10_code:
        parts.append(f"ICD-10 {icd10_code} {normalized_diagnosis}".strip())
    parts.append(normalized_diagnosis or diagnosis_label or body.dx)
    if body.tx:
        parts.append(f"treated with {body.tx}")
    if dx_text and dx_text not in {normalized_diagnosis, diagnosis_label}:
        parts.append(f"assessment text: {dx_text}")
    if body.hx:
        parts.append(f"history: {body.hx}")
    dx_summary = "; ".join(parts)

    case_context = {
        "dx": normalized_diagnosis or diagnosis_label or body.dx,
        "dx_text": dx_text,
        "diagnosis_label": diagnosis_label,
        "normalized_diagnosis": normalized_diagnosis,
        "tx": body.tx or "",
        "hx": body.hx or "",
        "icd_code": icd10_code,
        "icd10_code": icd10_code,
        "age": body.age,
        "sex": body.sex,
        "labs": body.labs or {}
    }

    filters = _merge_check_filters(body.filters or {}, icd10_code, normalized_diagnosis)

    result = await run_query(
        dx_summary=dx_summary,
        case_context=case_context,
        filters=filters,
        top_k=max(1, min(body.top_k, 50))
    )
    return result


# ──────────────────────────────────────────────
# GET /api/v1/rag/retrieval/{query_id}
# ──────────────────────────────────────────────

@router.get("/retrieval/{query_id}")
async def get_retrieval_log_endpoint(query_id: str):
    """
    查詢歷史 retrieval log 的完整內容（request、filters、hits、ebm_hits）。
    """
    if not query_id:
        raise HTTPException(status_code=400, detail="query_id is required")

    log = await sdb.get_retrieval_log(query_id)
    if not log:
        raise HTTPException(status_code=404, detail=f"Retrieval log '{query_id}' not found")

    def safe_parse(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    return {
        "query_id": query_id,
        "dx_summary": log.get("dx_summary"),
        "created_at": log.get("created_at"),
        "filters": safe_parse(log.get("filters_json")),
        "hits": safe_parse(log.get("hits_json")),
        "ebm_hits": safe_parse(log.get("ebm_hits_json"))
    }
