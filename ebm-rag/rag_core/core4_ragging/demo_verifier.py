# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core4_ragging/demo_verifier.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core4 查詢/驗證層，負責 retrieval、EBM 生成、ICD gate 與安全燈號。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core4_ragging/demo_verifier.py
# Timestamp: 2026-06-17 14:35 +08:00
# Version: v0.3
# Description: Demo-only verifier gate for synthetic EBM fallback.
#              LLM claim verification is advisory; deterministic scoring decides accept/reject.
#              Commercial builds can remove this module and its optional LAVA tasks without touching core RAG.
#              v0.3 adds conservative deterministic claim verification fallback
#              when the provider is rate-limited or unavailable.
# ----------------------------------------------------------------------------------------------------

from typing import Any

from rag_core.common.chunk_quality import summarize_chunk_quality
from rag_core.core4_ragging.traffic_light import (
    has_contraindication_caution,
    requires_contraindication_hard_gate
)

PASS_THRESHOLD = 85
REVIEW_THRESHOLD = 70
_GREEN_SIX_S = {"System", "Summaries", "Syntheses"}
_GREEN_OCEBM = {"Level_1", "Level_2"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on", "enabled"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return False


def demo_synthetic_fallback_enabled(filters: dict | None) -> bool:
    safe_filters = filters if isinstance(filters, dict) else {}
    return _as_bool(safe_filters.get("demo_synthetic_fallback"))


def _normalize_tokens(value: Any) -> set[str]:
    text = str(value or "").replace("_", " ").replace("-", " ").lower()
    token = ""
    tokens: set[str] = set()
    for char in text:
        if char.isalnum():
            token += char
            continue
        if len(token) >= 3:
            tokens.add(token)
        token = ""
    if len(token) >= 3:
        tokens.add(token)
    return tokens


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _chunk_payload(chunk: dict) -> dict:
    payload = chunk.get("payload")
    return payload if isinstance(payload, dict) else {}


def _chunk_value(chunk: dict, key: str) -> Any:
    value = chunk.get(key)
    if value not in (None, "", "unknown"):
        return value
    return _chunk_payload(chunk).get(key)


def _has_contraindication(chunks: list) -> bool:
    return has_contraindication_caution(chunks)


def _has_strong_evidence(chunks: list) -> bool:
    safe_chunks = chunks if isinstance(chunks, list) else []
    for chunk in safe_chunks:
        if not isinstance(chunk, dict):
            continue
        if _chunk_value(chunk, "six_s_level") in _GREEN_SIX_S:
            return True
        if _chunk_value(chunk, "ocebm_level") in _GREEN_OCEBM:
            return True
    return False


def _citation_coverage(chunks: list) -> float:
    safe_chunks = [chunk for chunk in chunks if isinstance(chunk, dict)] if isinstance(chunks, list) else []
    if not safe_chunks:
        return 0.0
    cited_count = 0
    for chunk in safe_chunks:
        if _chunk_value(chunk, "pmid") not in (None, "", "unknown") or _chunk_value(chunk, "doi") not in (None, "", "unknown"):
            cited_count += 1
    return cited_count / len(safe_chunks)


def _comment_sources(ebm_hits: dict) -> list[dict]:
    comments = ebm_hits.get("rag_comments") if isinstance(ebm_hits, dict) else []
    if not isinstance(comments, list):
        return []
    sources = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        raw_sources = comment.get("sources")
        if isinstance(raw_sources, list):
            sources.extend([source for source in raw_sources if isinstance(source, dict)])
    return sources


def _comment_claims(ebm_hits: dict) -> list[dict]:
    comments = ebm_hits.get("rag_comments") if isinstance(ebm_hits, dict) else []
    if not isinstance(comments, list):
        return []
    claims = []
    for index, comment in enumerate(comments[:12]):
        if not isinstance(comment, dict):
            continue
        sources = comment.get("sources")
        if not isinstance(sources, list):
            sources = []
        claims.append({
            "claim_index": index,
            "topic": str(comment.get("topic") or ""),
            "claim": str(comment.get("comment") or ""),
            "sources": [source for source in sources if isinstance(source, dict)]
        })
    return claims


def _source_chunk_text(chunks: list, chunk_ids: set[str]) -> str:
    safe_chunks = [chunk for chunk in chunks if isinstance(chunk, dict)] if isinstance(chunks, list) else []
    texts = []
    for chunk in safe_chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        if chunk_id in chunk_ids:
            payload = chunk.get("payload") if isinstance(chunk.get("payload"), dict) else {}
            # Source text often abbreviates the diagnosis (for example, AF),
            # while the cited metadata retains the normalized disease name.
            texts.append(" ".join([
                str(_chunk_value(chunk, "disease") or ""),
                str(chunk.get("text") or payload.get("text") or ""),
            ]))
    return " ".join(texts)


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "using", "used",
    "plan", "evidence", "guideline", "retrieved", "documented", "supports",
    "support", "therapy", "treatment", "patient"
}


def _claim_tokens(value: Any) -> set[str]:
    tokens = _normalize_tokens(value)
    return {token for token in tokens if token not in _STOPWORDS and len(token) >= 4}


def _deterministic_claim_verify(chunks: list, ebm_hits: dict, failure_reason: str) -> dict:
    claims = _comment_claims(ebm_hits)
    if not claims:
        return {
            "status": "failed",
            "error": "deterministic verifier requires ebm_hits.rag_comments",
            "claims": [],
            "unsupported_claim_count": 0,
            "contradiction_count": 0,
            "overall_claim_support": 0.0
        }

    normalized = []
    supported_count = 0
    unsupported_count = 0
    for claim in claims:
        chunk_ids = {str(source.get("chunk_id") or "").strip() for source in claim["sources"] if source.get("chunk_id")}
        evidence_text = _source_chunk_text(chunks, chunk_ids)
        claim_tokens = _claim_tokens(claim["claim"])
        evidence_tokens = _claim_tokens(evidence_text)
        overlap = len(claim_tokens.intersection(evidence_tokens))
        denominator = max(1, min(len(claim_tokens), 12))
        support_ratio = overlap / denominator
        has_traceable_source = bool(chunk_ids) and bool(evidence_text)
        if has_traceable_source and support_ratio >= 0.45:
            support = "supported"
            supported_count += 1
        else:
            support = "not_enough_evidence"
            unsupported_count += 1
        normalized.append({
            "claim_index": claim["claim_index"],
            "support": support,
            "supporting_chunk_ids": sorted(chunk_ids),
            "rationale": f"deterministic token overlap={support_ratio:.2f}; fallback_reason={failure_reason[:120]}",
            "risk": "low" if support == "supported" else "medium"
        })

    return {
        "status": "ok",
        "claims": normalized,
        "unsupported_claim_count": unsupported_count,
        "contradiction_count": 0,
        "overall_claim_support": supported_count / max(1, len(claims)),
        "model": {
            "provider": "deterministic",
            "model_id": "claim_overlap_fallback"
        },
        "fallback_reason": failure_reason
    }


def _source_diversity_score(ebm_hits: dict) -> int:
    source_ids = {str(source.get("chunk_id") or "") for source in _comment_sources(ebm_hits) if source.get("chunk_id")}
    if len(source_ids) >= 2:
        return 5
    if len(source_ids) == 1:
        return 3
    return 0


def _metadata_relevance(chunks: list, dx_summary: str, case_context: dict) -> tuple[bool, int]:
    safe_chunks = [chunk for chunk in chunks if isinstance(chunk, dict)] if isinstance(chunks, list) else []
    dx_text = f"{case_context.get('dx') or ''} {dx_summary or ''}"
    dx_tokens = _normalize_tokens(dx_text)
    if not safe_chunks or not dx_tokens:
        return False, 0

    best_overlap = 0
    for chunk in safe_chunks[:10]:
        disease_tokens = _normalize_tokens(_chunk_value(chunk, "disease"))
        if not disease_tokens:
            continue
        overlap = len(dx_tokens.intersection(disease_tokens))
        best_overlap = max(best_overlap, overlap)
        if disease_tokens.issubset(dx_tokens) or overlap >= max(1, min(2, len(disease_tokens))):
            return True, 3
    if best_overlap > 0:
        return True, 2
    return False, 0


def _retrieval_relevance_score(chunks: list, dx_summary: str, case_context: dict) -> int:
    safe_chunks = [chunk for chunk in chunks if isinstance(chunk, dict)] if isinstance(chunks, list) else []
    top_score = max((_safe_float(chunk.get("score")) for chunk in safe_chunks), default=0.0)
    if top_score >= 0.72:
        score = 22
    elif top_score >= 0.65:
        score = 18
    elif top_score >= 0.58:
        score = 10
    elif top_score >= 0.50:
        score = 5
    else:
        score = 0

    metadata_match, metadata_points = _metadata_relevance(safe_chunks, dx_summary, case_context)
    if metadata_match:
        return min(25, score + metadata_points)
    if top_score < 0.65:
        return min(score, 8)
    return score


def _source_quality_score(chunks: list, ebm_hits: dict) -> int:
    safe_chunks = [chunk for chunk in chunks if isinstance(chunk, dict)] if isinstance(chunks, list) else []
    if not safe_chunks:
        return 0
    score = 0
    quality = summarize_chunk_quality(safe_chunks)
    if quality.get("query_safe") is True:
        score += 4
    if _has_strong_evidence(safe_chunks):
        score += 6
    score += int(round(_citation_coverage(safe_chunks) * 5))
    score += _source_diversity_score(ebm_hits)
    return min(20, score)


def _schema_score(validation_errors: list[str] | None) -> int:
    errors = validation_errors if isinstance(validation_errors, list) else []
    return 10 if len(errors) == 0 else 0


def _claim_support_score(claim_verification: dict) -> int:
    if not isinstance(claim_verification, dict) or claim_verification.get("status") != "ok":
        return 0
    support = max(0.0, min(1.0, _safe_float(claim_verification.get("overall_claim_support"))))
    score = int(round(support * 30))
    unsupported = int(_safe_float(claim_verification.get("unsupported_claim_count"), 0.0))
    contradictions = int(_safe_float(claim_verification.get("contradiction_count"), 0.0))
    score -= unsupported * 6
    score -= contradictions * 12
    return max(0, min(30, score))


def _safety_consistency_score(chunks: list, case_context: dict, ebm_hits: dict, claim_verification: dict) -> int:
    light = str(ebm_hits.get("light_color") or "yellow").lower() if isinstance(ebm_hits, dict) else "yellow"
    hard_contraindication_present = requires_contraindication_hard_gate(chunks, case_context)
    if hard_contraindication_present and light != "orange":
        return 0
    if isinstance(claim_verification, dict) and int(_safe_float(claim_verification.get("contradiction_count"), 0.0)) > 0:
        return 0
    return 15


def _hard_failures(
    chunks: list,
    case_context: dict,
    ebm_hits: dict,
    validation_errors: list[str] | None,
    claim_verification: dict
) -> list[str]:
    reasons: list[str] = []
    safe_chunks = [chunk for chunk in chunks if isinstance(chunk, dict)] if isinstance(chunks, list) else []
    if not safe_chunks:
        reasons.append("no_retrieved_chunks")

    quality = summarize_chunk_quality(safe_chunks)
    if safe_chunks and quality.get("query_safe") is not True:
        reasons.append("retrieved_chunk_quality_gate_failed")

    errors = validation_errors if isinstance(validation_errors, list) else []
    if errors:
        reasons.append("schema_or_source_validation_failed")

    if requires_contraindication_hard_gate(safe_chunks, case_context) and str(ebm_hits.get("light_color") or "").lower() != "orange":
        reasons.append("contraindication_not_respected")

    comments = ebm_hits.get("rag_comments") if isinstance(ebm_hits, dict) else []
    if not isinstance(comments, list) or len(comments) == 0:
        reasons.append("no_rag_comments")

    if not isinstance(claim_verification, dict) or claim_verification.get("status") != "ok":
        reasons.append("claim_verifier_not_ready")
    else:
        if int(_safe_float(claim_verification.get("unsupported_claim_count"), 0.0)) > 0:
            reasons.append("unsupported_claims_present")
        if int(_safe_float(claim_verification.get("contradiction_count"), 0.0)) > 0:
            reasons.append("contradicted_claims_present")
        if _safe_float(claim_verification.get("overall_claim_support"), 0.0) < 0.85:
            reasons.append("claim_support_below_pass_threshold")

    return sorted(set(reasons))


async def run_claim_verifier(
    dx_summary: str,
    case_context: dict,
    chunks: list,
    ebm_hits: dict,
    candidate_kind: str
) -> dict:
    try:
        from lava.matching_tasks.claim_verify import execute_claim_verify
        result = await execute_claim_verify({
            "dx_summary": dx_summary,
            "case_context": case_context if isinstance(case_context, dict) else {},
            "chunks": chunks if isinstance(chunks, list) else [],
            "ebm_hits": ebm_hits if isinstance(ebm_hits, dict) else {},
            "candidate_kind": candidate_kind
        })
        if isinstance(result, dict) and result.get("status") == "ok":
            return result
        return _deterministic_claim_verify(chunks, ebm_hits, str(result.get("error") if isinstance(result, dict) else "claim_verify failed"))
    except Exception as e:
        return _deterministic_claim_verify(chunks, ebm_hits, str(e))


async def score_candidate(
    dx_summary: str,
    case_context: dict,
    chunks: list,
    ebm_hits: dict,
    candidate_kind: str,
    validation_errors: list[str] | None = None
) -> dict:
    safe_case_context = case_context if isinstance(case_context, dict) else {}
    safe_chunks = chunks if isinstance(chunks, list) else []
    safe_ebm_hits = ebm_hits if isinstance(ebm_hits, dict) else {}
    claim_verification = await run_claim_verifier(dx_summary, safe_case_context, safe_chunks, safe_ebm_hits, candidate_kind)

    components = {
        "evidence_relevance_score": _retrieval_relevance_score(safe_chunks, dx_summary, safe_case_context),
        "source_quality_score": _source_quality_score(safe_chunks, safe_ebm_hits),
        "claim_support_score": _claim_support_score(claim_verification),
        "safety_consistency_score": _safety_consistency_score(safe_chunks, safe_case_context, safe_ebm_hits, claim_verification),
        "output_schema_score": _schema_score(validation_errors)
    }
    total_score = sum(components.values())
    hard_fail_reasons = _hard_failures(safe_chunks, safe_case_context, safe_ebm_hits, validation_errors, claim_verification)

    if hard_fail_reasons:
        verdict = "reject"
        display_mode = "not_evaluable"
    elif total_score >= PASS_THRESHOLD:
        verdict = "pass"
        display_mode = "evidence_backed"
    elif total_score >= REVIEW_THRESHOLD:
        verdict = "review"
        display_mode = "demo_review_only"
    else:
        verdict = "reject"
        display_mode = "not_evaluable"

    return {
        "demo_only": True,
        "candidate_kind": candidate_kind,
        "verdict": verdict,
        "display_mode": display_mode,
        "score": total_score,
        "thresholds": {
            "pass": PASS_THRESHOLD,
            "review": REVIEW_THRESHOLD
        },
        "components": components,
        "hard_fail_reasons": hard_fail_reasons,
        "claim_verification": claim_verification
    }
