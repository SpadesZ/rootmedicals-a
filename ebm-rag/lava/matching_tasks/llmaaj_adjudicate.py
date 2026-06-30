# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/llmaaj_adjudicate.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/llmaaj_adjudicate.py
# Timestamp: 2026-06-15 21:08 +08:00
# Version: v0.1
# Description:
#   Optional LAVA matching task for llmxx-server Phase 4 LLMAAJ adjudication.
#   It scores semantic alignment between clinical parse and RAG EBM output.
# Change Notes:
#   - v0.1: Added strict score normalization, evidence digesting, and
#     JSON-only LLM adjudication prompt.
# Safety Notes:
#   - Scores are explanatory only. They never upgrade RAG traffic-light safety,
#     never bypass deterministic source validation, and never use outside
#     evidence beyond the provided RAG comments/chunks.
# Verification Notes:
#   - Covered by py_compile, LAVA readiness/module checks, llmxx-server replay,
#     and deterministic fallback tests when the task is unbound.
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import re
from typing import Any

from lava.adapter import get_adapter
from lava.llm_model import LLMModel


_MAX_COMMENTS = 10
_MAX_CHUNKS = 10


def _strip_json_fences(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    parts = text.split("```")
    if len(parts) >= 3:
        candidate = parts[1].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
        return candidate
    return text.lstrip("`").lstrip("json").strip()


def _load_json_object(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    text = _strip_json_fences(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as first_error:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None, str(first_error)
        else:
            return None, str(first_error)
    if not isinstance(parsed, dict):
        return None, "LLM returned non-object JSON"
    return parsed, None


def _clean_text(value: Any, max_length: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length]


def _safe_score(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return round(max(0.0, min(1.0, number)), 3)


def _clean_list(value: Any, max_items: int = 10, max_length: int = 120) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    output: list[str] = []
    for item in raw_items:
        text = _clean_text(item, max_length=max_length)
        if text and text not in output:
            output.append(text)
        if len(output) >= max_items:
            break
    return output


def _clinical_digest(value: Any) -> dict[str, Any]:
    clinical = value if isinstance(value, dict) else {}
    return {
        "dx": _clean_text(clinical.get("dx"), 500),
        "tx": _clean_text(clinical.get("tx"), 600),
        "hx": _clean_text(clinical.get("hx"), 900),
        "age": clinical.get("age") if isinstance(clinical.get("age"), int) else None,
        "sex": _clean_text(clinical.get("sex"), 32),
        "labs": clinical.get("labs") if isinstance(clinical.get("labs"), dict) else {},
    }


def _comments_digest(ebm_hits: dict[str, Any]) -> list[dict[str, Any]]:
    comments = ebm_hits.get("rag_comments") if isinstance(ebm_hits, dict) else []
    if not isinstance(comments, list):
        return []
    output: list[dict[str, Any]] = []
    for index, comment in enumerate(comments[:_MAX_COMMENTS]):
        if not isinstance(comment, dict):
            continue
        sources = comment.get("sources")
        if not isinstance(sources, list):
            sources = []
        output.append({
            "index": index,
            "topic": _clean_text(comment.get("topic"), 120),
            "comment": _clean_text(comment.get("comment"), 900),
            "source_chunk_ids": [
                _clean_text(source.get("chunk_id"), 120)
                for source in sources
                if isinstance(source, dict) and source.get("chunk_id")
            ],
        })
    return output


def _chunks_digest(ebm_hits: dict[str, Any]) -> list[dict[str, Any]]:
    retrieval = ebm_hits.get("retrieval") if isinstance(ebm_hits, dict) else {}
    chunks = retrieval.get("chunks") if isinstance(retrieval, dict) else []
    if not isinstance(chunks, list):
        return []
    output: list[dict[str, Any]] = []
    for chunk in chunks[:_MAX_CHUNKS]:
        if not isinstance(chunk, dict):
            continue
        output.append({
            "chunk_id": _clean_text(chunk.get("chunk_id"), 120),
            "paper_id": _clean_text(chunk.get("paper_id"), 120),
            "six_s_level": _clean_text(chunk.get("six_s_level"), 80),
            "ocebm_level": _clean_text(chunk.get("ocebm_level"), 80),
            "source_type": _clean_text(chunk.get("source_type"), 80),
            "contraindication": bool(chunk.get("contraindication")),
            "text": _clean_text(chunk.get("text"), 1300),
        })
    return output


def _fallback_scores(ebm_hits: dict[str, Any], reason: str) -> dict[str, Any]:
    light_color = _clean_text(ebm_hits.get("light_color"), 30).lower() if isinstance(ebm_hits, dict) else "yellow"
    comments = _comments_digest(ebm_hits if isinstance(ebm_hits, dict) else {})
    chunks = _chunks_digest(ebm_hits if isinstance(ebm_hits, dict) else {})
    base = {"green": 0.78, "yellow": 0.5, "orange": 0.18}.get(light_color, 0.35)
    if not comments or not chunks:
        base = min(base, 0.25)
    conflict = 0.85 if light_color == "orange" else 0.25
    risk = 0.9 if light_color == "orange" else 0.45
    return {
        "status": "fallback",
        "semantic_alignment_score": round(base, 3),
        "evidence_support_score": round(base, 3),
        "conflict_score": round(conflict, 3),
        "risk_score": round(risk, 3),
        "rationale": reason,
        "reason_codes": [reason],
    }


def _normalize_result(parsed: dict[str, Any], ebm_hits: dict[str, Any]) -> dict[str, Any]:
    reason_codes = _clean_list(parsed.get("reason_codes"), max_items=12, max_length=80)
    if not reason_codes:
        reason_codes = ["llmaaj_adjudicated"]
    normalized = {
        "status": "ok",
        "semantic_alignment_score": _safe_score(parsed.get("semantic_alignment_score"), 0.0),
        "evidence_support_score": _safe_score(parsed.get("evidence_support_score"), 0.0),
        "conflict_score": _safe_score(parsed.get("conflict_score"), 0.0),
        "risk_score": _safe_score(parsed.get("risk_score"), 0.0),
        "rationale": _clean_text(parsed.get("rationale"), 900),
        "reason_codes": reason_codes,
    }
    if not _comments_digest(ebm_hits):
        normalized["evidence_support_score"] = min(normalized["evidence_support_score"], 0.25)
        if "rag_comments_missing" not in normalized["reason_codes"]:
            normalized["reason_codes"].append("rag_comments_missing")
    if not _chunks_digest(ebm_hits):
        normalized["evidence_support_score"] = min(normalized["evidence_support_score"], 0.25)
        if "retrieval_chunks_missing" not in normalized["reason_codes"]:
            normalized["reason_codes"].append("retrieval_chunks_missing")
    return normalized


def _build_prompt(clinical: dict[str, Any], ebm_hits: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "You are an evidence adjudicator for a clinical RAG pipeline. "
        "Use only the supplied clinical parse, RAG comments, and retrieved chunks. "
        "Score semantic alignment and evidence support from 0.0 to 1.0. "
        "High conflict_score means the RAG answer conflicts with the case or evidence. "
        "High risk_score means the answer should require human review. Return only valid JSON."
    )
    payload = {
        "clinical_parse": _clinical_digest(clinical),
        "rag": {
            "query_id": _clean_text(ebm_hits.get("query_id"), 120),
            "light_color": _clean_text(ebm_hits.get("light_color"), 30),
            "short_comment": _clean_text(ebm_hits.get("short_comment") or ebm_hits.get("summary"), 900),
            "warnings": _clean_list(ebm_hits.get("warnings"), max_items=10, max_length=120),
            "rag_comments": _comments_digest(ebm_hits),
            "chunks": _chunks_digest(ebm_hits),
        },
        "required_json_schema": {
            "semantic_alignment_score": 0.0,
            "evidence_support_score": 0.0,
            "conflict_score": 0.0,
            "risk_score": 0.0,
            "rationale": "short reason",
            "reason_codes": ["short_machine_code"],
        },
    }
    content = f"[SYSTEM]\n{system}\n\nINPUT JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    return [{"role": "user", "content": content}]


async def execute_llmaaj_adjudicate(payload: dict) -> dict:
    safe_payload = payload if isinstance(payload, dict) else {}
    clinical = safe_payload.get("clinical_parse") if isinstance(safe_payload.get("clinical_parse"), dict) else {}
    ebm_hits = safe_payload.get("ebm_hits") if isinstance(safe_payload.get("ebm_hits"), dict) else {}
    if not ebm_hits:
        return _fallback_scores({}, "ebm_hits_missing")

    conn = LLMModel.get_connection_for_task("llmaaj_adjudicate")
    if not conn:
        fallback = _fallback_scores(ebm_hits, "llmaaj_adjudicate_unconfigured")
        fallback["error"] = "llmaaj_adjudicate has no ready verified LAVA binding"
        fallback["model"] = None
        fallback["connection_id"] = None
        return fallback

    conn = dict(conn)
    adapter = get_adapter(conn.get("provider", ""))
    if not adapter:
        fallback = _fallback_scores(ebm_hits, "unknown_provider")
        fallback["status"] = "failed"
        fallback["error"] = f"Unknown provider: {conn.get('provider')}"
        return fallback

    try:
        result = await adapter.chat(conn["api_key"], conn["model_id"], _build_prompt(clinical, ebm_hits), temperature=0.0, max_tokens=1600)
        parsed, parse_error = _load_json_object(result.get("content", ""))
        if parsed is None:
            fallback = _fallback_scores(ebm_hits, "llmaaj_invalid_json")
            fallback.update({
                "status": "failed",
                "error": f"llmaaj_adjudicate returned invalid JSON: {parse_error}",
                "model": {"provider": conn.get("provider"), "model_id": conn.get("model_id")},
                "connection_id": conn.get("id"),
            })
            return fallback
        normalized = _normalize_result(parsed, ebm_hits)
        normalized["model"] = {"provider": conn.get("provider"), "model_id": conn.get("model_id")}
        normalized["connection_id"] = conn.get("id")
        return normalized
    except Exception as exc:
        fallback = _fallback_scores(ebm_hits, "llmaaj_exception")
        fallback.update({
            "status": "failed",
            "error": adapter.safe_error(exc, conn.get("api_key", "")),
            "model": {"provider": conn.get("provider"), "model_id": conn.get("model_id")},
            "connection_id": conn.get("id"),
        })
        return fallback
