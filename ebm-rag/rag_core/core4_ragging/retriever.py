# 模組定位: ebm-rag Core4 hybrid retrieval 與 query-decomposition policy。
# 主要責任: 建立 deterministic/LLM-assisted query plan，執行分層 filters 與 bounded fallback。
# 呼叫來源: legacy query/check/ebm_generate 與 Topic content 每個 slot 的 evidence retrieval。
# 輸入契約: diagnosis/context、safe filters、top_k；LLM expansion 只可新增合法查詢字串。
# 輸出契約: hits、phase/query plan、filter/fallback metadata；expansion 失敗仍回 deterministic plan。
# 安全邊界: 不因 Topic flow 放寬 OCEBM/6S/source gates；無 collection/readiness 時明確回報。
# 維護提醒: 共用 retrieval 變更需同時跑 legacy route 與 Topic contract，避免只修單一路徑。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core4_ragging/retriever.py
# Timestamp: 2026-06-16
# Version: v0.8
# Description: Core4 Hybrid Retriever。
#              單一 dx_summary 拆成 deterministic 4 個 sub-query，必要時可選 LLM 擴充。
#              v0.8 優先使用 rag_query_strategy task，失敗時回退 deterministic。
#              v0.9 查詢優先使用 ICD-10 code + normalized diagnosis，A 欄
#              原文只作為 assessment context，不再單靠 OCR 診斷字串。
#              支援 min_ocebm、6S、臨床 metadata filter、collection gate 與條件式 fallback phases。
# ----------------------------------------------------------------------------------------------------

import asyncio
from typing import Any

from qdrant_client.http.models import Filter, FieldCondition, MatchAny, MatchValue

from rag_core.common.errors import UnconfiguredError, UnsupportedProviderError
from rag_core.common.evidence_scope import canonical_topic_key, slot_key_from_id
from rag_core.core2_embeddings.embedding_client import embed_texts
from rag_core.core3_vector_store.collections import collection_name
from rag_core.core3_vector_store.qdrant_client import get_qdrant_client

_SIX_S_TOP = ["System", "Summaries", "Syntheses"]
_SIX_S_ALL = ["System", "Summaries", "Syntheses", "Synopses", "Studies"]
_OCEBM_LEVELS = ["Level_1", "Level_2", "Level_3", "Level_4", "Level_5"]
_QUERY_DECOMPOSITION_MODES = {"deterministic", "llm_assisted"}
_QUERY_DECOMPOSE_TIMEOUT_SECONDS = 18
_MAX_RETRIEVAL_QUERIES = 8


def _normalize_ocebm_level(level: str | None) -> str | None:
    if level is None:
        return None
    normalized = str(level).strip().replace(" ", "_")
    if not normalized:
        return None
    if normalized.lower().startswith("level_"):
        suffix = normalized.split("_", 1)[1]
        if suffix.isdigit():
            normalized = f"Level_{suffix}"
    if normalized not in _OCEBM_LEVELS:
        raise ValueError(f"Invalid min_ocebm: {level}")
    return normalized


def _allowed_ocebm_levels(min_ocebm: str | None, allow_unknown: bool = False) -> list[str] | None:
    if min_ocebm is None:
        levels = list(_OCEBM_LEVELS)
    else:
        normalized = _normalize_ocebm_level(min_ocebm)
        max_index = _OCEBM_LEVELS.index(normalized)
        levels = _OCEBM_LEVELS[:max_index + 1]
    if allow_unknown:
        levels.append("unknown")
    return levels


def _as_clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"unknown", "null", "none"}:
        return None
    return text


def _as_bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _normalize_filters(filters: dict) -> dict:
    safe_filters = dict(filters or {})
    if "min_ocebm" not in safe_filters and "min_ocebm_level" in safe_filters:
        safe_filters["min_ocebm"] = safe_filters.get("min_ocebm_level")
    if "min_ocebm_level" not in safe_filters and "min_ocebm" in safe_filters:
        safe_filters["min_ocebm_level"] = safe_filters.get("min_ocebm")
    return safe_filters


def _build_queries(dx_summary: str, case_context: dict) -> list[str]:
    icd_code = _as_clean_string(case_context.get("icd10_code")) or _as_clean_string(case_context.get("icd_code"))
    normalized_dx = (
        _as_clean_string(case_context.get("normalized_diagnosis"))
        or _as_clean_string(case_context.get("diagnosis_label"))
        or _as_clean_string(case_context.get("dx"))
        or dx_summary
    )
    dx = f"{icd_code} {normalized_dx}".strip() if icd_code else normalized_dx
    tx = _as_clean_string(case_context.get("tx")) or ""
    explicit_queries = case_context.get("retrieval_queries")
    if not isinstance(explicit_queries, list):
        explicit_queries = []
    return [
        *explicit_queries,
        f"{dx} guideline diagnosis criteria",
        f"{tx} efficacy for {dx}" if tx else f"{dx} guideline treatment efficacy",
        f"{tx} contraindications adverse effects {dx}" if tx else f"{dx} contraindications adverse effects",
        f"{dx} alternative treatments"
    ]


def _normalize_query_text(query: Any) -> str | None:
    if not isinstance(query, str):
        return None
    normalized = " ".join(query.split()).strip()
    if len(normalized) < 8 or len(normalized) > 220:
        return None
    if "{" in normalized or "}" in normalized or "[" in normalized or "]" in normalized:
        return None
    return normalized


def _query_key(query: str) -> str:
    return "".join(char.lower() for char in query if char.isalnum())


def _dedupe_queries(queries: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = _normalize_query_text(query)
        if normalized is None:
            continue
        key = _query_key(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
        if len(deduped) >= _MAX_RETRIEVAL_QUERIES:
            break
    return deduped


def _query_decomposition_mode(filters: dict) -> str:
    requested = str(filters.get("query_decomposition_mode") or filters.get("query_mode") or "deterministic").strip().lower()
    if requested not in _QUERY_DECOMPOSITION_MODES:
        return "deterministic"
    return requested


async def _build_query_plan(dx_summary: str, case_context: dict, filters: dict) -> dict:
    base_queries = _dedupe_queries(_build_queries(dx_summary, case_context))
    mode = _query_decomposition_mode(filters)
    plan = {
        "mode": mode,
        "source": "deterministic",
        "base_queries": base_queries,
        "llm_queries": [],
        "queries": base_queries,
        "llm_status": "not_requested" if mode == "deterministic" else "pending",
        "llm_model": None,
        "connection_id": None,
        "llm_task_id": "rag_query_strategy" if mode == "llm_assisted" else None,
        "fallback_reason": None
    }
    if mode != "llm_assisted":
        return plan

    try:
        from lava.matching_tasks.rag_query_strategy import execute_rag_query_strategy
        result = await asyncio.wait_for(
            execute_rag_query_strategy({
                "dx_summary": dx_summary,
                "case_context": case_context,
                "base_queries": base_queries
            }),
            timeout=_QUERY_DECOMPOSE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        plan["llm_status"] = "timeout"
        plan["fallback_reason"] = f"rag_query_strategy timed out after {_QUERY_DECOMPOSE_TIMEOUT_SECONDS}s"
        return plan
    except Exception as e:
        plan["llm_status"] = "failed"
        plan["fallback_reason"] = str(e)
        return plan

    if not isinstance(result, dict):
        plan["llm_status"] = "failed"
        plan["fallback_reason"] = "query_decompose returned invalid result"
        return plan

    plan["llm_status"] = str(result.get("status") or "unknown")
    plan["llm_model"] = result.get("model")
    plan["connection_id"] = result.get("connection_id")
    extra_queries = _dedupe_queries(list(result.get("queries", [])))
    combined_queries = _dedupe_queries(base_queries + extra_queries)
    accepted_extra = combined_queries[len(base_queries):]
    if result.get("status") == "ok" and accepted_extra:
        plan["source"] = "deterministic_plus_llm"
        plan["llm_queries"] = accepted_extra
        plan["queries"] = combined_queries
        plan["fallback_reason"] = None
        return plan

    error = result.get("error") or "no_valid_extra_queries"
    plan["llm_queries"] = []
    plan["queries"] = base_queries
    plan["fallback_reason"] = str(error)
    return plan


def _append_metadata_filters(conditions: list[FieldCondition], filters: dict, include_source_type: bool) -> None:
    specialty = _as_clean_string(filters.get("specialty"))
    disease = _as_clean_string(filters.get("disease"))
    source_type = _as_clean_string(filters.get("source_type"))
    is_guideline = _as_bool_or_none(filters.get("is_guideline"))
    has_contraindication_terms = _as_bool_or_none(filters.get("has_contraindication_terms"))
    topic_key = canonical_topic_key(filters.get("topic_key"))
    slot_key = slot_key_from_id(filters.get("slot_key"))

    if specialty:
        conditions.append(FieldCondition(key="specialty", match=MatchValue(value=specialty)))
    if disease:
        conditions.append(FieldCondition(key="disease", match=MatchValue(value=disease)))
    if include_source_type and source_type:
        conditions.append(FieldCondition(key="source_type", match=MatchValue(value=source_type)))
    if is_guideline is not None:
        conditions.append(FieldCondition(key="is_guideline", match=MatchValue(value=is_guideline)))
    if has_contraindication_terms is not None:
        conditions.append(FieldCondition(key="has_contraindication_terms", match=MatchValue(value=has_contraindication_terms)))
    if topic_key != "*":
        conditions.append(FieldCondition(key="topic_key", match=MatchValue(value=topic_key)))
    if slot_key != "*":
        conditions.append(FieldCondition(key="slot_keys", match=MatchValue(value=slot_key)))


def _qdrant_filter(
    six_s_levels: list[str] | None = None,
    min_ocebm: str | None = None,
    filters: dict | None = None,
    allow_unknown_ocebm: bool = False,
    include_source_type: bool = True
) -> Filter | None:
    filters = filters or {}
    conditions: list[FieldCondition] = [
        FieldCondition(key="quality_status", match=MatchValue(value="ok")),
        # Source lifecycle is a mandatory safety filter and survives every bounded fallback phase.
        FieldCondition(key="source_lifecycle_status", match=MatchValue(value="current")),
    ]

    if six_s_levels:
        conditions.append(FieldCondition(key="six_s_level", match=MatchAny(any=six_s_levels)))

    allowed_ocebm = _allowed_ocebm_levels(min_ocebm, allow_unknown=allow_unknown_ocebm)
    if allowed_ocebm:
        conditions.append(FieldCondition(key="ocebm_level", match=MatchAny(any=allowed_ocebm)))

    _append_metadata_filters(conditions, filters, include_source_type=include_source_type)

    if not conditions:
        return None
    return Filter(must=conditions)


def _phase_summary(name: str, six_s_levels: list[str] | None, min_ocebm: str | None, filters: dict, allow_unknown_ocebm: bool, include_source_type: bool) -> dict:
    return {
        "name": name,
        "six_s_levels": six_s_levels or [],
        "min_ocebm": min_ocebm,
        "allow_unknown_ocebm": allow_unknown_ocebm,
        "include_source_type": include_source_type,
        "filters": {
            "specialty": filters.get("specialty"),
            "disease": filters.get("disease"),
            "source_type": filters.get("source_type") if include_source_type else None,
            "is_guideline": filters.get("is_guideline"),
            "has_contraindication_terms": filters.get("has_contraindication_terms"),
            "topic_key": filters.get("topic_key"),
            "slot_key": filters.get("slot_key"),
        }
    }


def _merge_hits(seen: dict, result_sets: list, phase_name: str) -> tuple[int, list[str]]:
    inserted_or_updated = 0
    errors: list[str] = []
    for result_set in result_sets:
        if isinstance(result_set, Exception):
            errors.append(str(result_set))
            continue
        for hit in result_set:
            payload = hit.payload or {}
            chunk_id = payload.get("chunk_id", str(hit.id))
            if not chunk_id:
                continue
            current_score = float(hit.score or 0.0)
            if chunk_id not in seen or current_score > seen[chunk_id]["score"]:
                seen[chunk_id] = {
                    "chunk_id": chunk_id,
                    "paper_id": payload.get("paper_id"),
                    "text": payload.get("text", ""),
                    "score": current_score,
                    "pmid": payload.get("pmid"),
                    "doi": payload.get("doi"),
                    "six_s_level": payload.get("six_s_level", "unknown"),
                    "ocebm_level": payload.get("ocebm_level", "unknown"),
                    "grade_baseline": payload.get("grade_baseline", "unknown"),
                    "has_contraindication_terms": payload.get("has_contraindication_terms", False),
                    "section_title": payload.get("section_title", ""),
                    "title_path": payload.get("title_path", []),
                    "retrieval_phase": phase_name,
                    "payload": payload
                }
                inserted_or_updated += 1
    return inserted_or_updated, errors


def _search_qdrant_points(client, collection: str, vector: list[float], query_filter: Filter | None, limit: int) -> list:
    if hasattr(client, "query_points"):
        result = client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True
        )
        return list(getattr(result, "points", []) or [])

    if hasattr(client, "search"):
        return list(client.search(
            collection_name=collection,
            query_vector=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True
        ) or [])

    raise RuntimeError("Qdrant client does not support query_points or search")


async def retrieve(
    dx_summary: str,
    case_context: dict = None,
    filters: dict = None,
    top_k: int = 10
) -> dict:
    case_context = case_context or {}
    filters = _normalize_filters(filters or {})
    safe_top_k = max(1, min(int(top_k or 10), 50))
    query_plan = await _build_query_plan(dx_summary, case_context, filters)
    queries = query_plan["queries"]

    min_ocebm = filters.get("min_ocebm")
    try:
        if min_ocebm is not None:
            min_ocebm = _normalize_ocebm_level(min_ocebm)
    except ValueError as e:
        return {
            "status": "invalid_filter",
            "error": str(e),
            "hits": [],
            "queries": queries,
            "query_plan": query_plan,
            "phases": []
        }

    try:
        embedding_result = await embed_texts(queries)
    except (UnconfiguredError, UnsupportedProviderError) as e:
        return {
            "status": "unconfigured",
            "error": str(e),
            "hits": [],
            "queries": queries,
            "query_plan": query_plan,
            "phases": []
        }
    except Exception as e:
        return {
            "status": "embedding_failed",
            "error": str(e),
            "hits": [],
            "queries": queries,
            "query_plan": query_plan,
            "phases": []
        }

    vectors = embedding_result.get("vectors", [])
    embedding_model = embedding_result.get("model")
    embedding_provider = embedding_result.get("provider")
    embedding_dim = embedding_result.get("dim")

    if not vectors or not embedding_model or not embedding_provider or not embedding_dim:
        return {
            "status": "embedding_failed",
            "error": "Embedding result is empty or missing model metadata",
            "hits": [],
            "queries": queries,
            "query_plan": query_plan,
            "phases": []
        }

    collection = collection_name(embedding_provider, embedding_model, int(embedding_dim))
    client = get_qdrant_client()
    try:
        existing_collections = {item.name for item in client.get_collections().collections}
    except Exception as e:
        return {
            "status": "qdrant_unavailable",
            "error": str(e),
            "hits": [],
            "queries": queries,
            "query_plan": query_plan,
            "phases": [],
            "collection": collection,
            "embedding": {
                "provider": embedding_provider,
                "model": embedding_model,
                "dim": embedding_dim
            }
        }
    if collection not in existing_collections:
        return {
            "status": "collection_not_found",
            "error": f"Qdrant collection not found for active embedding model: {collection}",
            "hits": [],
            "queries": queries,
            "query_plan": query_plan,
            "phases": [],
            "collection": collection,
            "embedding": {
                "provider": embedding_provider,
                "model": embedding_model,
                "dim": embedding_dim
            }
        }
    seen: dict = {}
    phases: list[dict] = []
    prefer_levels = filters.get("prefer_six_s_levels") or _SIX_S_TOP
    if not isinstance(prefer_levels, list) or not prefer_levels:
        prefer_levels = _SIX_S_TOP

    async def search_one(vector, query_filter: Filter | None):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: _search_qdrant_points(client, collection, vector, query_filter, safe_top_k)
        )

    async def run_phase(
        name: str,
        six_s_levels: list[str] | None,
        allow_unknown_ocebm: bool,
        include_source_type: bool,
        active_filters: dict | None = None,
    ) -> None:
        phase_filters = filters if active_filters is None else active_filters
        phase_filter = _qdrant_filter(
            six_s_levels=six_s_levels,
            min_ocebm=min_ocebm,
            filters=phase_filters,
            allow_unknown_ocebm=allow_unknown_ocebm,
            include_source_type=include_source_type
        )
        result_sets = await asyncio.gather(
            *[search_one(vector, phase_filter) for vector in vectors],
            return_exceptions=True
        )
        inserted_or_updated, errors = _merge_hits(seen, result_sets, phase_name=name)
        phase_data = _phase_summary(
            name=name,
            six_s_levels=six_s_levels,
            min_ocebm=min_ocebm,
            filters=phase_filters,
            allow_unknown_ocebm=allow_unknown_ocebm,
            include_source_type=include_source_type
        )
        phase_data["updated_hits"] = inserted_or_updated
        phase_data["unique_hits_after_phase"] = len(seen)
        phase_data["errors"] = errors
        phases.append(phase_data)

    await run_phase("phase1_strict_preferred", prefer_levels, allow_unknown_ocebm=False, include_source_type=True)

    if len(seen) < safe_top_k:
        await run_phase("phase2_all_6s_known_ocebm", _SIX_S_ALL, allow_unknown_ocebm=False, include_source_type=True)

    if len(seen) < safe_top_k:
        await run_phase("phase3_clinical_metadata_unknown_ocebm", None, allow_unknown_ocebm=True, include_source_type=False)

    if not seen and any(filters.get(key) for key in ("disease", "specialty", "source_type")):
        # ponytail: local taxonomy labels are not guaranteed to match indexed
        # metadata. Only after every strict phase is empty, fall back to the
        # semantic query while retaining safety/evidence filters.
        relaxed_filters = {
            key: value for key, value in filters.items()
            if key not in {"disease", "specialty", "source_type"}
        }
        await run_phase(
            "phase4_semantic_fallback",
            None,
            allow_unknown_ocebm=True,
            include_source_type=False,
            active_filters=relaxed_filters,
        )

    ranked_hits = sorted(seen.values(), key=lambda item: item["score"], reverse=True)[:safe_top_k]
    status = "ok"
    if not ranked_hits:
        status = "empty"

    return {
        "status": status,
        "error": None,
        "hits": ranked_hits,
        "queries": queries,
        "query_plan": query_plan,
        "phases": phases,
        "collection": collection,
        "embedding": {
            "provider": embedding_provider,
            "model": embedding_model,
            "dim": embedding_dim
        }
    }
