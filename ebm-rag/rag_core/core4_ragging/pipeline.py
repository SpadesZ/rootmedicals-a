# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core4_ragging/pipeline.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.2-展示驗證升綠修正
# 說明: RAG Core4 查詢/驗證層，負責 retrieval、EBM 生成、ICD gate 與安全燈號。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# 維護筆記:
#   - production live RAG 不在這裡被自動升級；只有使用者明確開啟 demo_synthetic_fallback 時，
#     並且 verifier、ICD gate、來源追溯都通過，才允許 synthetic candidate 顯示 green。
#   - llmxx-server final gate 只做保守降級，所以本檔必須把 RAG 自己確認過的燈號說清楚。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core4_ragging/pipeline.py
# Timestamp: 2026-06-17 14:45 +08:00
# Version: v1.2
# Description: Core4 RAG Pipeline 入口。
#              retrieve → local calculators → ebm_generate → always-on traffic_light hard rules → retrieval_log。
#              檢索失敗、schema/source validation 失敗時回傳 not_evaluable，避免醫療查詢誤用。
#              v1.1: Apply traffic-light gates, including ICD gate, to empty or
#              not-ready retrieval baselines before returning.
#              v1.2: In explicit demo fallback mode, allow verifier-pass output
#              to become green/evidence-backed after source validation.
# ----------------------------------------------------------------------------------------------------

import uuid

from rag_core.common import state_db as sdb
from rag_core.common.chunk_quality import summarize_chunk_quality
from rag_core.core4_ragging.calculators import run_requested_calculators
from rag_core.core4_ragging.demo_verifier import demo_synthetic_fallback_enabled, score_candidate
from rag_core.core4_ragging.retriever import retrieve
from rag_core.core4_ragging.traffic_light import apply_traffic_light

_VALID_LIGHTS = {"green", "yellow", "orange"}
_VALID_EVIDENCE_LEVELS = {"Level_1", "Level_2", "Level_3", "Level_4", "Level_5", "unknown"}
_VALID_GRADES = {"Grade_A", "Grade_B", "Grade_C", "unknown"}


def _baseline_ebm_hits(query_id: str, status: str, error: str | None, retrieval_payload: dict, filters: dict, calculators: list) -> dict:
    short_comment = error or status or "EBM generation is not available"
    return {
        "query_id": query_id,
        "status": status,
        "error": error,
        "light_color": "yellow",
        "llmaaj_score": 0,
        "short_comment": short_comment,
        "rag_comments": [],
        "alternatives": [],
        "warnings": [],
        "calculators": calculators,
        "retrieval": {
            "status": retrieval_payload.get("status"),
            "error": retrieval_payload.get("error"),
            "queries": retrieval_payload.get("queries", []),
            "query_plan": retrieval_payload.get("query_plan", {}),
            "phases": retrieval_payload.get("phases", []),
            "filters": filters,
            "chunks": retrieval_payload.get("hits", []),
            "collection": retrieval_payload.get("collection"),
            "embedding": retrieval_payload.get("embedding")
        }
    }


def _normal_optional_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "unknown"}:
        return None
    return text


def _schema_validation_errors(ebm_hits: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(ebm_hits, dict):
        return ["ebm_hits must be an object"]

    light_color = str(ebm_hits.get("light_color") or "").strip().lower()
    if light_color not in _VALID_LIGHTS:
        errors.append("light_color must be green, yellow, or orange")
    else:
        ebm_hits["light_color"] = light_color

    if not isinstance(ebm_hits.get("short_comment"), str):
        errors.append("short_comment must be a string")

    warnings = ebm_hits.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    score = ebm_hits.get("llmaaj_score")
    if score is None:
        inferred_score = {"green": 90, "yellow": 60, "orange": 30}.get(light_color, 60)
        ebm_hits["llmaaj_score"] = inferred_score
        warnings.append({
            "code": "llmaaj_score_inferred",
            "message": "LLM output omitted llmaaj_score; deterministic fallback score was applied"
        })
        ebm_hits["warnings"] = warnings
    elif isinstance(score, bool) or not isinstance(score, (int, float)):
        errors.append("llmaaj_score must be numeric")
    else:
        safe_score = max(0, min(int(round(float(score))), 100))
        ebm_hits["llmaaj_score"] = safe_score

    rag_comments = ebm_hits.get("rag_comments")
    if not isinstance(rag_comments, list):
        errors.append("rag_comments must be a list")
        rag_comments = []

    if not isinstance(ebm_hits.get("alternatives", []), list):
        errors.append("alternatives must be a list")
    if not isinstance(ebm_hits.get("warnings", []), list):
        errors.append("warnings must be a list")

    for index, comment in enumerate(rag_comments):
        if not isinstance(comment, dict):
            errors.append(f"rag_comments[{index}] must be an object")
            continue
        if not isinstance(comment.get("topic", ""), str):
            errors.append(f"rag_comments[{index}].topic must be a string")
        if not isinstance(comment.get("comment", ""), str):
            errors.append(f"rag_comments[{index}].comment must be a string")
        evidence_level = comment.get("evidence_level", "unknown")
        if evidence_level not in _VALID_EVIDENCE_LEVELS:
            errors.append(f"rag_comments[{index}].evidence_level is invalid")
        grade = comment.get("grade", "unknown")
        if grade not in _VALID_GRADES:
            errors.append(f"rag_comments[{index}].grade is invalid")
        sources = comment.get("sources", [])
        if not isinstance(sources, list):
            errors.append(f"rag_comments[{index}].sources must be a list")
            continue
        for source_index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"rag_comments[{index}].sources[{source_index}] must be an object")
                continue
            score = source.get("score")
            if score is not None and (not isinstance(score, (int, float)) or isinstance(score, bool)):
                errors.append(f"rag_comments[{index}].sources[{source_index}].score must be numeric")
    return errors


def _apply_score_light_mapping(ebm_hits: dict) -> dict:
    if not isinstance(ebm_hits, dict):
        return {}
    current_light = str(ebm_hits.get("light_color", "yellow")).lower()
    if current_light == "orange":
        ebm_hits["light_color"] = "orange"
        return ebm_hits
    score = ebm_hits.get("llmaaj_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return ebm_hits
    safe_score = max(0, min(int(round(float(score))), 100))
    ebm_hits["llmaaj_score"] = safe_score
    if safe_score < 60:
        ebm_hits["light_color"] = "yellow"
    elif safe_score >= 90:
        ebm_hits["light_color"] = "green"
    else:
        ebm_hits["light_color"] = "yellow"
    return ebm_hits


def _source_validation_errors(chunks: list, ebm_hits: dict) -> list[str]:
    available_chunks = {
        str(chunk.get("chunk_id")): chunk
        for chunk in chunks
        if chunk.get("chunk_id")
    }
    errors: list[str] = []
    rag_comments = ebm_hits.get("rag_comments", [])
    if not isinstance(rag_comments, list):
        return ["rag_comments must be a list"]

    for index, comment in enumerate(rag_comments):
        if not isinstance(comment, dict):
            errors.append(f"rag_comments[{index}] must be an object")
            continue
        comment_text = str(comment.get("comment") or "").strip()
        sources = comment.get("sources", [])
        if comment_text and not isinstance(sources, list):
            errors.append(f"rag_comments[{index}].sources must be a list")
            continue
        if comment_text and len(sources) == 0:
            errors.append(f"rag_comments[{index}] has clinical comment without source")
            continue
        for source_index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"rag_comments[{index}].sources[{source_index}] must be an object")
                continue
            chunk_id = str(source.get("chunk_id") or "").strip()
            if not chunk_id:
                errors.append(f"rag_comments[{index}].sources[{source_index}] missing chunk_id")
                continue
            if chunk_id not in available_chunks:
                errors.append(f"rag_comments[{index}].sources[{source_index}] chunk_id not in retrieved chunks: {chunk_id}")
                continue

            chunk = available_chunks[chunk_id]
            for citation_key in ["pmid", "doi"]:
                provided_value = _normal_optional_text(source.get(citation_key))
                expected_value = _normal_optional_text(chunk.get(citation_key))
                if provided_value and not expected_value:
                    errors.append(f"rag_comments[{index}].sources[{source_index}].{citation_key} not present in retrieved chunk")
                elif provided_value and expected_value and provided_value != expected_value:
                    errors.append(f"rag_comments[{index}].sources[{source_index}].{citation_key} mismatch for chunk_id {chunk_id}")
    return errors


def _deterministic_retry_filters(filters: dict) -> dict:
    retry_filters = dict(filters or {})
    retry_filters["query_decomposition_mode"] = "deterministic"
    return retry_filters


def _used_llm_query_expansion(filters: dict, retrieval_payload: dict) -> bool:
    requested_mode = str(filters.get("query_decomposition_mode") or filters.get("query_mode") or "deterministic").lower()
    query_plan = retrieval_payload.get("query_plan", {})
    if not isinstance(query_plan, dict):
        return False
    return requested_mode == "llm_assisted" and query_plan.get("source") == "deterministic_plus_llm"


def _append_query_fallback_warning(ebm_hits: dict, source_query_id: str, reason: str | None) -> dict:
    if not isinstance(ebm_hits, dict):
        return ebm_hits
    warnings = ebm_hits.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings.append({
        "code": "llm_query_decomposition_result_fallback",
        "message": "LLM-assisted query expansion produced a non-usable generation result; deterministic query mode was returned",
        "source_query_id": source_query_id,
        "reason": reason or "not_evaluable"
    })
    ebm_hits["warnings"] = warnings
    return ebm_hits


def _append_warning(ebm_hits: dict, warning: dict) -> dict:
    if not isinstance(ebm_hits, dict):
        return ebm_hits
    warnings = ebm_hits.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings.append(warning)
    ebm_hits["warnings"] = warnings
    return ebm_hits


def _has_traceable_comment_source(ebm_hits: dict) -> bool:
    comments = ebm_hits.get("rag_comments")
    if not isinstance(comments, list):
        return False
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        sources = comment.get("sources")
        if isinstance(sources, list) and sources:
            return True
    return False


def _demo_verifier_passed(demo_verifier: dict) -> bool:
    if not isinstance(demo_verifier, dict):
        return False
    try:
        score = float(demo_verifier.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    hard_fail_reasons = demo_verifier.get("hard_fail_reasons")
    if not isinstance(hard_fail_reasons, list):
        hard_fail_reasons = []
    return str(demo_verifier.get("verdict") or "").lower() == "pass" and score >= 85 and not hard_fail_reasons


def _promote_demo_verifier_pass_to_green(ebm_hits: dict, demo_verifier: dict) -> dict:
    # 程式筆記：這不是「看到 verifier pass 就放行」。demo/synthetic 綠燈仍要先過
    # traffic_light 的 ICD gate，且至少有一筆 rag_comment.sources 可以追到 chunk。
    # ICD 缺失會留在 yellow；ICD/A 或 ICD/P 明顯錯配會留在 orange。
    if not _demo_verifier_passed(demo_verifier):
        return ebm_hits
    if not _has_traceable_comment_source(ebm_hits):
        return ebm_hits
    icd_gate = ebm_hits.get("icd_gate") if isinstance(ebm_hits.get("icd_gate"), dict) else {}
    if str(icd_gate.get("status") or "").lower() != "pass":
        return ebm_hits
    if str(ebm_hits.get("light_color") or "").lower() == "orange":
        return ebm_hits

    ebm_hits["light_color"] = "green"
    ebm_hits["display_mode"] = "evidence_backed"
    current_score = ebm_hits.get("llmaaj_score")
    if isinstance(current_score, bool) or not isinstance(current_score, (int, float)) or current_score < 90:
        ebm_hits["llmaaj_score"] = max(90, int(float(demo_verifier.get("score") or 90)))
    if not str(ebm_hits.get("short_comment") or "").strip():
        ebm_hits["short_comment"] = "Verifier-gated evidence-backed output is available."
    return _append_warning(ebm_hits, {
        "code": "demo_verifier_green_promotion",
        "message": "Explicit synthetic/demo mode passed verifier, ICD gate, and source traceability checks; green display is allowed for this validated demo path",
    })


def _demo_candidate_already_guarded(ebm_hits: dict) -> bool:
    if not isinstance(ebm_hits, dict):
        return False
    if ebm_hits.get("demo_candidate_kind") == "synthetic_ebm_candidate":
        return True
    warnings = ebm_hits.get("warnings")
    if not isinstance(warnings, list):
        return False
    guarded_codes = {
        "demo_synthetic_candidate_rejected",
        "demo_synthetic_candidate_failed"
    }
    return any(isinstance(warning, dict) and warning.get("code") in guarded_codes for warning in warnings)


def _attach_query_context(
    ebm_hits: dict,
    query_id: str,
    retrieval_payload: dict,
    filters: dict,
    calculator_results: list
) -> dict:
    ebm_hits["query_id"] = query_id
    ebm_hits["status"] = "ok"
    ebm_hits["error"] = None
    ebm_hits["calculators"] = calculator_results
    ebm_hits["retrieval"] = {
        "status": retrieval_payload.get("status"),
        "error": retrieval_payload.get("error"),
        "queries": retrieval_payload.get("queries", []),
        "query_plan": retrieval_payload.get("query_plan", {}),
        "phases": retrieval_payload.get("phases", []),
        "filters": filters,
        "chunks": retrieval_payload.get("hits", []),
        "collection": retrieval_payload.get("collection"),
        "embedding": retrieval_payload.get("embedding")
    }
    return ebm_hits


async def _run_synthetic_demo_candidate(
    query_id: str,
    dx_summary: str,
    safe_case_context: dict,
    generation_context: dict,
    safe_filters: dict,
    retrieval_payload: dict,
    chunks: list,
    calculator_results: list,
    failure_reason: str
) -> dict:
    from lava.matching_tasks.synthetic_ebm_candidate import execute_synthetic_ebm_candidate

    gen_result = await execute_synthetic_ebm_candidate({
        "query_id": query_id,
        "dx_summary": dx_summary,
        "case_context": generation_context,
        "chunks": chunks,
        "failure_reason": failure_reason
    })

    if gen_result.get("status") == "ok" and isinstance(gen_result.get("ebm_hits"), dict):
        candidate = _attach_query_context(
            gen_result["ebm_hits"],
            query_id,
            retrieval_payload,
            safe_filters,
            calculator_results
        )
        candidate["demo_candidate_kind"] = "synthetic_ebm_candidate"
        candidate = _append_warning(candidate, {
            "code": "demo_synthetic_candidate_generated",
            "message": "Demo-only synthetic candidate generated after normal RAG generation could not be used",
            "failure_reason": failure_reason
        })

        schema_errors = _schema_validation_errors(candidate)
        source_errors = _source_validation_errors(chunks, candidate)
        validation_errors = schema_errors + source_errors
        if not validation_errors:
            candidate = _apply_score_light_mapping(candidate)
            candidate = apply_traffic_light(chunks, candidate, safe_case_context)

        demo_verifier = await score_candidate(
            dx_summary=dx_summary,
            case_context=safe_case_context,
            chunks=chunks,
            ebm_hits=candidate,
            candidate_kind="synthetic_ebm_candidate",
            validation_errors=validation_errors
        )
        candidate["demo_verifier"] = demo_verifier

        if demo_verifier.get("verdict") == "pass" and not validation_errors:
            candidate = _promote_demo_verifier_pass_to_green(candidate, demo_verifier)
            candidate = _append_warning(candidate, {
                "code": "demo_synthetic_candidate_accepted",
                "message": "Demo-only synthetic candidate passed verifier gate; remove this path for commercial production"
            })
            return candidate

        baseline = _baseline_ebm_hits(
            query_id=query_id,
            status="not_evaluable",
            error="Demo synthetic candidate did not pass verifier gate",
            retrieval_payload=retrieval_payload,
            filters=safe_filters,
            calculators=calculator_results
        )
        baseline = _append_warning(baseline, {
            "code": "demo_synthetic_candidate_rejected",
            "message": "Synthetic candidate was generated for demo observability only and was not accepted as evidence-backed output",
            "failure_reason": failure_reason,
            "verdict": demo_verifier.get("verdict"),
            "score": demo_verifier.get("score")
        })
        if validation_errors:
            baseline = _append_warning(baseline, {
                "code": "demo_candidate_validation_failed",
                "message": "Synthetic candidate failed schema or source validation before verifier scoring",
                "errors": validation_errors
            })
        baseline["demo_candidate"] = candidate
        baseline["demo_verifier"] = demo_verifier
        return apply_traffic_light(chunks, baseline, safe_case_context)

    baseline = _baseline_ebm_hits(
        query_id=query_id,
        status="not_evaluable",
        error=gen_result.get("error") or "synthetic_ebm_candidate unavailable",
        retrieval_payload=retrieval_payload,
        filters=safe_filters,
        calculators=calculator_results
    )
    baseline = _append_warning(baseline, {
        "code": "demo_synthetic_candidate_failed",
        "message": "Demo-only synthetic candidate task failed or is unconfigured",
        "failure_reason": failure_reason,
        "provider_error": gen_result.get("error")
    })
    baseline["demo_candidate"] = {
        "status": gen_result.get("status"),
        "error": gen_result.get("error"),
        "ebm_hits": None
    }
    return apply_traffic_light(chunks, baseline, safe_case_context)


async def _apply_demo_verifier_gate(
    query_id: str,
    dx_summary: str,
    safe_case_context: dict,
    safe_filters: dict,
    retrieval_payload: dict,
    chunks: list,
    calculator_results: list,
    ebm_hits: dict
) -> dict:
    demo_verifier = await score_candidate(
        dx_summary=dx_summary,
        case_context=safe_case_context,
        chunks=chunks,
        ebm_hits=ebm_hits,
        candidate_kind="retrieved_ebm_generation",
        validation_errors=[]
    )
    ebm_hits["demo_verifier"] = demo_verifier
    ebm_hits = _append_warning(ebm_hits, {
        "code": "demo_verifier_gate_evaluated",
        "message": "Demo verifier gate evaluated retrieved EBM generation; remove this gate for commercial production",
        "verdict": demo_verifier.get("verdict"),
        "score": demo_verifier.get("score")
    })
    if demo_verifier.get("verdict") == "pass":
        ebm_hits = _promote_demo_verifier_pass_to_green(ebm_hits, demo_verifier)
        return ebm_hits

    baseline = _baseline_ebm_hits(
        query_id=query_id,
        status="not_evaluable",
        error="Demo verifier gate rejected EBM output",
        retrieval_payload=retrieval_payload,
        filters=safe_filters,
        calculators=calculator_results
    )
    baseline = _append_warning(baseline, {
        "code": "demo_verifier_gate_rejected",
        "message": "Retrieved EBM generation was blocked by demo verifier gate",
        "verdict": demo_verifier.get("verdict"),
        "score": demo_verifier.get("score"),
        "hard_fail_reasons": demo_verifier.get("hard_fail_reasons", [])
    })
    baseline["demo_candidate"] = {
        "candidate_kind": "retrieved_ebm_generation",
        "ebm_hits": ebm_hits
    }
    baseline["demo_verifier"] = demo_verifier
    return apply_traffic_light(chunks, baseline, safe_case_context)


async def run_query(dx_summary: str, case_context: dict = None, filters: dict = None, top_k: int = 10) -> dict:
    query_id = str(uuid.uuid4())
    safe_case_context = dict(case_context or {})
    safe_filters = dict(filters or {})
    safe_top_k = max(1, min(int(top_k or 10), 50))
    demo_fallback_enabled = demo_synthetic_fallback_enabled(safe_filters)

    calculator_results = run_requested_calculators(safe_case_context)
    generation_context = dict(safe_case_context)
    generation_context["calculator_results"] = calculator_results

    retrieval_payload = await retrieve(dx_summary, safe_case_context, safe_filters, safe_top_k)
    chunks = retrieval_payload.get("hits", [])

    if retrieval_payload.get("status") != "ok" or not chunks:
        if demo_fallback_enabled:
            ebm_hits = await _run_synthetic_demo_candidate(
                query_id=query_id,
                dx_summary=dx_summary,
                safe_case_context=safe_case_context,
                generation_context=generation_context,
                safe_filters=safe_filters,
                retrieval_payload=retrieval_payload,
                chunks=chunks,
                calculator_results=calculator_results,
                failure_reason=retrieval_payload.get("error") or f"retrieval_status={retrieval_payload.get('status')}"
            )
        else:
            ebm_hits = _baseline_ebm_hits(
                query_id=query_id,
                status="not_evaluable",
                error=retrieval_payload.get("error") or f"retrieval_status={retrieval_payload.get('status')}",
                retrieval_payload=retrieval_payload,
                filters=safe_filters,
                calculators=calculator_results
            )
            ebm_hits["warnings"].append({
                "code": "retrieval_not_ready_or_empty",
                "message": "RAG retrieval did not produce usable evidence chunks"
            })
        ebm_hits = apply_traffic_light(chunks, ebm_hits, safe_case_context)
        await sdb.save_retrieval_log(
            query_id,
            dx_summary,
            {
                "dx_summary": dx_summary,
                "case_context": safe_case_context,
                "top_k": safe_top_k
            },
            safe_filters,
            chunks,
            ebm_hits
        )
        return ebm_hits

    retrieval_quality = summarize_chunk_quality(chunks)
    if retrieval_quality.get("query_safe") is not True:
        ebm_hits = _baseline_ebm_hits(
            query_id=query_id,
            status="not_evaluable",
            error="Retrieved chunks failed quality gate",
            retrieval_payload=retrieval_payload,
            filters=safe_filters,
            calculators=calculator_results
        )
        ebm_hits["warnings"].append({
            "code": "retrieved_chunk_quality_gate_failed",
            "message": "Retrieved evidence chunks are not safe for clinical EBM generation",
            "quality": retrieval_quality
        })
        ebm_hits = apply_traffic_light(chunks, ebm_hits, safe_case_context)
        await sdb.save_retrieval_log(
            query_id,
            dx_summary,
            {
                "dx_summary": dx_summary,
                "case_context": safe_case_context,
                "top_k": safe_top_k
            },
            safe_filters,
            chunks,
            ebm_hits
        )
        return ebm_hits

    from lava.matching_tasks.ebm_generate import execute_ebm_generate
    gen_result = await execute_ebm_generate({
        "query_id": query_id,
        "dx_summary": dx_summary,
        "case_context": generation_context,
        "chunks": chunks
    })

    if gen_result.get("status") == "ok" and isinstance(gen_result.get("ebm_hits"), dict):
        ebm_hits = gen_result["ebm_hits"]
        ebm_hits = _attach_query_context(ebm_hits, query_id, retrieval_payload, safe_filters, calculator_results)
        schema_errors = _schema_validation_errors(ebm_hits)
        source_errors = _source_validation_errors(chunks, ebm_hits)
        validation_errors = schema_errors + source_errors
        if validation_errors:
            if demo_fallback_enabled:
                ebm_hits = await _run_synthetic_demo_candidate(
                    query_id=query_id,
                    dx_summary=dx_summary,
                    safe_case_context=safe_case_context,
                    generation_context=generation_context,
                    safe_filters=safe_filters,
                    retrieval_payload=retrieval_payload,
                    chunks=chunks,
                    calculator_results=calculator_results,
                    failure_reason=f"EBM validation failed: {'; '.join(validation_errors[:4])}"
                )
            else:
                ebm_hits = _baseline_ebm_hits(
                    query_id=query_id,
                    status="not_evaluable",
                    error="EBM validation failed",
                    retrieval_payload=retrieval_payload,
                    filters=safe_filters,
                    calculators=calculator_results
                )
                ebm_hits["warnings"].append({
                    "code": "ebm_hits_validation_failed",
                    "message": "Generated EBM result failed schema or source traceability validation",
                    "errors": validation_errors
                })
        else:
            ebm_hits = _apply_score_light_mapping(ebm_hits)
    else:
        generation_status = gen_result.get("status", "failed") if isinstance(gen_result, dict) else "failed"
        generation_error = gen_result.get("error") if isinstance(gen_result, dict) else "Invalid generation result"
        if demo_fallback_enabled:
            ebm_hits = await _run_synthetic_demo_candidate(
                query_id=query_id,
                dx_summary=dx_summary,
                safe_case_context=safe_case_context,
                generation_context=generation_context,
                safe_filters=safe_filters,
                retrieval_payload=retrieval_payload,
                chunks=chunks,
                calculator_results=calculator_results,
                failure_reason=generation_error or generation_status
            )
        else:
            ebm_hits = _baseline_ebm_hits(
                query_id=query_id,
                status="not_evaluable" if generation_status == "failed" else generation_status,
                error=generation_error,
                retrieval_payload=retrieval_payload,
                filters=safe_filters,
                calculators=calculator_results
            )
            ebm_hits["warnings"].append({
                "code": "ebm_generation_failed",
                "message": "LLM EBM generation failed; retrieval hits and deterministic calculator results were preserved",
                "provider_error": generation_error
            })

    if ebm_hits.get("status") != "ok" and _used_llm_query_expansion(safe_filters, retrieval_payload):
        retry_result = await run_query(
            dx_summary,
            safe_case_context,
            _deterministic_retry_filters(safe_filters),
            safe_top_k
        )
        return _append_query_fallback_warning(
            retry_result,
            source_query_id=query_id,
            reason=ebm_hits.get("error") or ebm_hits.get("status")
        )

    if not _demo_candidate_already_guarded(ebm_hits):
        ebm_hits = apply_traffic_light(chunks, ebm_hits, safe_case_context)
    if demo_fallback_enabled and ebm_hits.get("status") == "ok" and ebm_hits.get("demo_candidate_kind") != "synthetic_ebm_candidate":
        ebm_hits = await _apply_demo_verifier_gate(
            query_id=query_id,
            dx_summary=dx_summary,
            safe_case_context=safe_case_context,
            safe_filters=safe_filters,
            retrieval_payload=retrieval_payload,
            chunks=chunks,
            calculator_results=calculator_results,
            ebm_hits=ebm_hits
        )

    await sdb.save_retrieval_log(
        query_id,
        dx_summary,
        {
            "dx_summary": dx_summary,
            "case_context": safe_case_context,
            "top_k": safe_top_k
        },
        safe_filters,
        chunks,
        ebm_hits
    )
    return ebm_hits
