# 模組定位: ebm-rag 的 Topic content 三階段 orchestrator。
# 主要責任: vision plan 後逐 slot 呼叫既有 retrieve，再以 evidence-only composer 產生 component JSON。
# 呼叫來源: Core5 /api/v1/rag/topic-content/generate endpoint 與 contract tests。
# 輸入契約: 已驗證 manifest、1 至 3 張截圖、可選 slot/filter/top_k。
# 輸出契約: manifest-matched sections、planning_mode；失敗帶結構化 error，成功內容附 evidence digest。
# 安全邊界: composer 只能引用 retrieval bundle 內 paper/chunk；錯誤截斷至 500 字元。
# 維護提醒: schema-invalid composer fallback 要寫入 retrieval log；provider 真錯誤仍算 section failure。
# ----------------------------------------------------------------------------------------------------

import hashlib
import json
import uuid

from lava.matching_tasks.topic_content_compose import (
    EVIDENCE_SCHEMA,
    execute_topic_content_compose,
    validate_evidence_bundle,
)
from lava.matching_tasks.topic_content_plan import execute_topic_content_plan, validate_topic_manifest
from rag_core.common import state_db as sdb
from rag_core.common.evidence_scope import canonical_topic_key, slot_key_from_id
from rag_core.core4_ragging.retriever import retrieve


def _selected_manifest(manifest: dict, only_slot_ids: list[str]) -> dict:
    validated = validate_topic_manifest(manifest)
    target_ids = {slot["slot_id"] for slot in validated["slots"] if slot["content_target"]}
    requested = list(only_slot_ids or [])
    if len(set(requested)) != len(requested):
        raise ValueError("only_slot_ids contains duplicates")
    unknown = set(requested).difference(target_ids)
    if unknown:
        raise ValueError(f"only_slot_ids contains unknown or non-content slots: {', '.join(sorted(unknown))}")
    selected_ids = set(requested) if requested else target_ids
    selected_slots = [slot for slot in validated["slots"] if slot["slot_id"] in selected_ids]
    if not selected_slots:
        raise ValueError("manifest has no selected content-target slots")
    return {**validated, "slots": selected_slots}


def _section_context(manifest: dict, section: dict) -> tuple[str, dict]:
    slot = next(item for item in manifest["slots"] if item["slot_id"] == section["slot_id"])
    path = " > ".join(slot["heading_path"])
    needs = "; ".join(section["evidence_needs"])
    dx_summary = f"{manifest['topic_name']} | section: {path} | evidence needs: {needs}"
    case_context = {
        "dx": manifest["topic_name"],
        "normalized_diagnosis": manifest["topic_name"],
        "tx": "",
        # Topic planner needs are already schema-bounded; pass them explicitly so Core4 does not collapse every slot to generic disease queries.
        "retrieval_queries": list(section["evidence_needs"]),
    }
    return dx_summary, case_context


def _evidence_bundle(section: dict, query_id: str, retrieval: dict) -> dict:
    hits = []
    remaining_text_budget = 120_000
    for hit in retrieval.get("hits", []):
        payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else {}
        paper_id = hit.get("paper_id")
        chunk_id = hit.get("chunk_id")
        text = str(hit.get("text") or "").strip()
        if not paper_id or not chunk_id or not text or remaining_text_budget < 1:
            continue
        # ponytail: cap prompt evidence at 120k characters; add model-aware token budgeting if larger contexts are required.
        text = text[:min(12000, remaining_text_budget)]
        remaining_text_budget -= len(text)
        hits.append({
            "paper_id": str(paper_id),
            "chunk_id": str(chunk_id),
            "title": str(payload.get("title") or payload.get("guideline_title") or hit.get("section_title") or ""),
            "source_type": str(payload.get("source_type") or "unknown"),
            "six_s_level": str(hit.get("six_s_level") or "unknown"),
            "ocebm_level": str(hit.get("ocebm_level") or "unknown"),
            "is_guideline": bool(payload.get("is_guideline", False)),
            "score": float(hit.get("score") or 0.0),
            "text": text,
        })
    phase_names = [
        str(phase.get("name")) for phase in retrieval.get("phases", [])
        if isinstance(phase, dict) and phase.get("name")
    ]
    bundle = {
        "schema": EVIDENCE_SCHEMA,
        "slot_id": section["slot_id"],
        "query_id": query_id,
        "queries": list(retrieval.get("queries", []))[:8],
        "phases": phase_names[:3],
        "coverage": {
            # ponytail: retrieval cannot prove semantic need coverage; the composer reports missing needs.
            "status": "candidate_evidence" if hits else "insufficient",
            "missing_needs": [] if hits else list(section["evidence_needs"]),
        },
        "hits": hits,
    }
    return validate_evidence_bundle(bundle, expected_slot_id=section["slot_id"])


def _evidence_digest(bundle: dict) -> str:
    payload = json.dumps(bundle.get("hits", []), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def generate_topic_content(
    manifest: dict,
    screenshots: list[dict],
    only_slot_ids: list[str] | None = None,
    filters: dict | None = None,
    top_k: int = 10,
) -> dict:
    selected_manifest = _selected_manifest(manifest, only_slot_ids or [])
    plan_result = await execute_topic_content_plan({
        "manifest": selected_manifest,
        "screenshots": screenshots,
    })
    if plan_result.get("status") != "ok" or not isinstance(plan_result.get("plan"), dict):
        return {
            "status": plan_result.get("status") or "failed",
            "manifest_hash": selected_manifest["dom_hash"],
            "plan": None,
            "sections": [],
            "errors": [{"stage": "plan", "error": plan_result.get("error") or "topic plan failed"}],
        }

    plan = plan_result["plan"]
    sections = []
    errors = []
    request_filters = dict(filters or {})
    safe_top_k = max(1, min(int(top_k or 10), 50))
    for section in plan["sections"]:
        query_id = str(uuid.uuid4())
        dx_summary, case_context = _section_context(selected_manifest, section)
        section_filters = {**section.get("filters", {}), **request_filters}
        # Topic content may relax taxonomy labels, but never its reviewed topic/slot evidence scope.
        section_filters["topic_key"] = canonical_topic_key(selected_manifest["topic_name"])
        section_filters["slot_key"] = slot_key_from_id(section["slot_id"])
        section_filters["query_decomposition_mode"] = "llm_assisted"
        try:
            retrieval = await retrieve(
                dx_summary=dx_summary,
                case_context=case_context,
                filters=section_filters,
                top_k=min(safe_top_k, section["top_k"]),
            )
            bundle = _evidence_bundle(section, query_id, retrieval)
            compose_result = await execute_topic_content_compose({
                "section": section,
                "evidence_bundle": bundle,
            })
            content = compose_result.get("content")
            section_status = compose_result.get("status") or "failed"
            fallback_reason = compose_result.get("fallback_reason")
            section_error = None
            if content is None:
                section_error = {
                    "slot_id": section["slot_id"],
                    "stage": "compose",
                    "error": compose_result.get("error") or "topic compose failed",
                }
                errors.append(section_error)
            sections.append({
                "slot_id": section["slot_id"],
                "status": section_status,
                "query_id": query_id,
                "evidence_digest": _evidence_digest(bundle),
                "content": content,
                "fallback_reason": fallback_reason,
                "error": {
                    "stage": section_error["stage"],
                    "error": section_error["error"],
                } if section_error else None,
            })
            try:
                await sdb.save_retrieval_log(
                    query_id,
                    dx_summary,
                    {
                        "topic_uid": selected_manifest["topic_uid"],
                        "slot_id": section["slot_id"],
                        "evidence_needs": section["evidence_needs"],
                        "top_k": min(safe_top_k, section["top_k"]),
                        "compose_fallback_reason": fallback_reason,
                    },
                    section_filters,
                    bundle["hits"],
                    content or {"status": section_status, "error": compose_result.get("error")},
                )
            except Exception as error:
                errors.append({
                    "slot_id": section["slot_id"],
                    "stage": "retrieval_log",
                    "error": str(error)[:500],
                })
        except Exception as error:
            safe_error = str(error)[:500]
            section_error = {"stage": "retrieval", "error": safe_error}
            sections.append({
                "slot_id": section["slot_id"],
                "status": "failed",
                "query_id": query_id,
                "content": None,
                "error": section_error,
            })
            errors.append({"slot_id": section["slot_id"], **section_error})

    return {
        "status": "ok" if not errors else ("partial" if any(item["content"] for item in sections) else "failed"),
        "manifest_hash": selected_manifest["dom_hash"],
        "plan": plan,
        "planning_mode": plan_result.get("planning_mode", "single"),
        "sections": sections,
        "errors": errors,
    }
