# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/claim_verify.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/claim_verify.py
# Timestamp: 2026-06-11
# Version: v0.1
# Description: Demo-only LAVA matching task — claim-to-evidence verification.
#              The LLM judges support between generated EBM claims and retrieved chunks only.
#              Final accept/reject decisions remain deterministic in demo_verifier.py.
# ----------------------------------------------------------------------------------------------------

import json

from lava.adapter import get_adapter
from lava.llm_model import LLMModel

_ALLOWED_SUPPORT = {"supported", "partially_supported", "unsupported", "contradicted", "not_enough_evidence"}


def _strip_json_fences(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.strip().lower().startswith("json"):
                text = text.strip()[4:]
        else:
            text = text.lstrip("`")
            if text.lower().startswith("json"):
                text = text[4:]
    return text.strip()


def _safe_float(value, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _chunk_digest(chunks: list) -> list[dict]:
    safe_chunks = chunks if isinstance(chunks, list) else []
    out = []
    for chunk in safe_chunks[:10]:
        if not isinstance(chunk, dict):
            continue
        out.append({
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "paper_id": chunk.get("paper_id"),
            "pmid": chunk.get("pmid"),
            "doi": chunk.get("doi"),
            "six_s_level": chunk.get("six_s_level"),
            "ocebm_level": chunk.get("ocebm_level"),
            "score": chunk.get("score"),
            "text": str(chunk.get("text") or "")[:1400]
        })
    return out


def _claim_digest(ebm_hits: dict) -> list[dict]:
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
            "cited_chunk_ids": [
                str(source.get("chunk_id") or "")
                for source in sources
                if isinstance(source, dict) and source.get("chunk_id")
            ]
        })
    return claims


def _normalize_result(payload: dict, claim_count: int) -> dict:
    safe_payload = payload if isinstance(payload, dict) else {}
    raw_claims = safe_payload.get("claims")
    claims = raw_claims if isinstance(raw_claims, list) else []
    normalized_claims = []
    unsupported_count = 0
    contradiction_count = 0
    supported_weight = 0.0

    for index in range(claim_count):
        raw_item = claims[index] if index < len(claims) and isinstance(claims[index], dict) else {}
        support = str(raw_item.get("support") or "not_enough_evidence").strip().lower()
        if support not in _ALLOWED_SUPPORT:
            support = "not_enough_evidence"
        if support == "supported":
            supported_weight += 1.0
        elif support == "partially_supported":
            supported_weight += 0.5
            unsupported_count += 1
        elif support == "contradicted":
            contradiction_count += 1
            unsupported_count += 1
        else:
            unsupported_count += 1

        supporting_chunk_ids = raw_item.get("supporting_chunk_ids")
        if not isinstance(supporting_chunk_ids, list):
            supporting_chunk_ids = []
        normalized_claims.append({
            "claim_index": index,
            "support": support,
            "supporting_chunk_ids": [str(item) for item in supporting_chunk_ids if str(item).strip()],
            "rationale": str(raw_item.get("rationale") or "")[:600],
            "risk": str(raw_item.get("risk") or "unknown")[:80]
        })

    inferred_support = (supported_weight / claim_count) if claim_count > 0 else 0.0
    overall_support = _safe_float(safe_payload.get("overall_claim_support"), inferred_support)
    return {
        "status": "ok",
        "claims": normalized_claims,
        "unsupported_claim_count": int(safe_payload.get("unsupported_claim_count", unsupported_count) or unsupported_count),
        "contradiction_count": int(safe_payload.get("contradiction_count", contradiction_count) or contradiction_count),
        "overall_claim_support": overall_support
    }


def _build_prompt(payload: dict) -> list[dict]:
    dx_summary = str(payload.get("dx_summary") or "")
    case_context = payload.get("case_context") if isinstance(payload.get("case_context"), dict) else {}
    candidate_kind = str(payload.get("candidate_kind") or "unknown")
    chunks = _chunk_digest(payload.get("chunks", []))
    claims = _claim_digest(payload.get("ebm_hits", {}))

    system = """You verify whether generated clinical EBM claims are supported by retrieved evidence chunks.
Rules:
1. Use ONLY the provided chunks. Do not use outside medical knowledge.
2. Judge each claim against the cited chunks and the chunk text.
3. Mark support as supported, partially_supported, unsupported, contradicted, or not_enough_evidence.
4. If a claim uses a source chunk that does not actually support it, mark unsupported or contradicted.
5. Return ONLY valid JSON with this schema:
{
  "claims": [
    {
      "claim_index": 0,
      "support": "supported|partially_supported|unsupported|contradicted|not_enough_evidence",
      "supporting_chunk_ids": ["chunk-id"],
      "rationale": "short reason",
      "risk": "low|medium|high"
    }
  ],
  "unsupported_claim_count": 0,
  "contradiction_count": 0,
  "overall_claim_support": 0.0
}"""
    user_payload = {
        "candidate_kind": candidate_kind,
        "dx_summary": dx_summary,
        "case_context": case_context,
        "chunks": chunks,
        "claims": claims
    }
    return [{"role": "user", "content": f"[SYSTEM]\n{system}\n\nPayload:\n{json.dumps(user_payload, ensure_ascii=False)}"}]


async def execute_claim_verify(payload: dict) -> dict:
    safe_payload = payload if isinstance(payload, dict) else {}
    claims = _claim_digest(safe_payload.get("ebm_hits", {}))
    if not claims:
        return {
            "status": "failed",
            "error": "claim_verify requires ebm_hits.rag_comments",
            "claims": [],
            "unsupported_claim_count": 0,
            "contradiction_count": 0,
            "overall_claim_support": 0.0
        }

    conn = LLMModel.get_connection_for_task("claim_verify")
    if not conn:
        return {
            "status": "unconfigured",
            "error": "claim_verify has no ready verified LAVA binding",
            "claims": [],
            "unsupported_claim_count": len(claims),
            "contradiction_count": 0,
            "overall_claim_support": 0.0
        }

    conn = dict(conn)
    adapter = get_adapter(conn.get("provider", ""))
    if not adapter:
        return {
            "status": "failed",
            "error": f"Unknown provider: {conn.get('provider')}",
            "claims": [],
            "unsupported_claim_count": len(claims),
            "contradiction_count": 0,
            "overall_claim_support": 0.0
        }

    try:
        result = await adapter.chat(conn["api_key"], conn["model_id"], _build_prompt(safe_payload), temperature=0.0, max_tokens=4096)
        parsed = json.loads(_strip_json_fences(result.get("content", "")))
        normalized = _normalize_result(parsed, len(claims))
        normalized["model"] = {"provider": conn["provider"], "model_id": conn["model_id"]}
        normalized["connection_id"] = conn.get("id")
        return normalized
    except json.JSONDecodeError as e:
        return {
            "status": "failed",
            "error": f"claim_verify returned invalid JSON: {e}",
            "claims": [],
            "unsupported_claim_count": len(claims),
            "contradiction_count": 0,
            "overall_claim_support": 0.0
        }
    except Exception as e:
        return {
            "status": "failed",
            "error": adapter.safe_error(e, conn.get("api_key", "")),
            "claims": [],
            "unsupported_claim_count": len(claims),
            "contradiction_count": 0,
            "overall_claim_support": 0.0
        }
