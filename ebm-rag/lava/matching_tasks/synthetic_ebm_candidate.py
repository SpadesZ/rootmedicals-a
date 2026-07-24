# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/synthetic_ebm_candidate.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/synthetic_ebm_candidate.py
# Timestamp: 2026-06-17 14:35 +08:00
# Version: v0.2
# Description: Demo-only LAVA matching task — synthetic EBM candidate generation.
#              This task may draft a candidate when normal generation fails, but it is not evidence.
#              The candidate must still pass schema, source validation, claim verification, and deterministic gate.
#              v0.2 adds a deterministic candidate only when the LLM path fails,
#              using retrieved chunk metadata as the sole evidence source.
# ----------------------------------------------------------------------------------------------------

import json

from lava.adapter import get_adapter
from lava.llm_model import LLMModel
from rag_core.core4_ragging.traffic_light import requires_contraindication_hard_gate


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
            "grade_baseline": chunk.get("grade_baseline"),
            "score": chunk.get("score"),
            "has_contraindication_terms": chunk.get("has_contraindication_terms"),
            "text": str(chunk.get("text") or "")[:1400]
        })
    return out


def _first_traceable_chunk(chunks: list) -> dict | None:
    safe_chunks = chunks if isinstance(chunks, list) else []
    for chunk in safe_chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        if not chunk_id:
            continue
        if chunk.get("pmid") not in (None, "", "unknown") or chunk.get("doi") not in (None, "", "unknown"):
            return chunk
    return safe_chunks[0] if safe_chunks and isinstance(safe_chunks[0], dict) else None


def _clean_text(value, default: str = "") -> str:
    text = " ".join(str(value or "").split())
    return text if text else default


def _source_from_chunk(chunk: dict) -> dict:
    return {
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "pmid": chunk.get("pmid"),
        "doi": chunk.get("doi"),
        "six_s_level": chunk.get("six_s_level") or "unknown",
        "ocebm_level": chunk.get("ocebm_level") or "unknown",
        "score": float(chunk.get("score") or 0.0)
    }


def _deterministic_candidate(payload: dict, fallback_reason: str) -> dict | None:
    safe_payload = payload if isinstance(payload, dict) else {}
    chunks = safe_payload.get("chunks", [])
    case_context = safe_payload.get("case_context") if isinstance(safe_payload.get("case_context"), dict) else {}
    hard_contraindication = requires_contraindication_hard_gate(chunks, case_context)
    chunk = next((item for item in chunks if isinstance(item, dict) and item.get("has_contraindication_terms") and _first_traceable_chunk([item])), None) if hard_contraindication else None
    chunk = chunk or _first_traceable_chunk(chunks)
    if not chunk:
        return None
    dx = _clean_text(case_context.get("normalized_diagnosis") or case_context.get("dx") or safe_payload.get("dx_summary"), "the documented diagnosis")
    tx = _clean_text(case_context.get("tx"), "the documented treatment plan")
    source = _source_from_chunk(chunk)
    score = float(chunk.get("score") or 0.0)
    strong_source = source["six_s_level"] in {"System", "Summaries", "Syntheses"} or source["ocebm_level"] in {"Level_1", "Level_2"}
    light = "orange" if hard_contraindication else ("green" if strong_source and score >= 0.60 and (source.get("pmid") or source.get("doi")) else "yellow")
    llmaaj_score = 92 if light == "green" else 72
    grade_baseline = str(chunk.get("grade_baseline") or "unknown")
    grade = grade_baseline if grade_baseline in {"Grade_A", "Grade_B", "Grade_C", "unknown"} else f"Grade_{grade_baseline}" if grade_baseline in {"A", "B", "C"} else "unknown"
    comment = (
        "Retrieved guideline states that anticoagulation decisions must account for absolute contraindications and balance bleeding against stroke risk."
        if hard_contraindication
        else f"Retrieved guideline evidence supports {tx} for {dx}."
    )
    return {
        "light_color": light,
        "llmaaj_score": llmaaj_score,
        "short_comment": "Contraindication and bleeding-risk evidence conflicts with immediate anticoagulation; urgent clinical review is required." if hard_contraindication else f"Traceable guideline evidence supports {tx} for {dx}.",
        "rag_comments": [
            {
                "topic": "Contraindication and bleeding risk" if hard_contraindication else "Evidence fit",
                "comment": comment,
                "evidence_level": source["ocebm_level"],
                "grade": grade,
                "sources": [source]
            }
        ],
        "alternatives": [],
        "warnings": [
            {
                "code": "deterministic_demo_candidate_used",
                "message": "LLM demo candidate generation failed; deterministic candidate used retrieved source metadata only.",
                "failure_reason": fallback_reason
            }
        ]
    }


def _build_prompt(payload: dict) -> list[dict]:
    safe_payload = payload if isinstance(payload, dict) else {}
    dx_summary = str(safe_payload.get("dx_summary") or "")
    case_context = safe_payload.get("case_context") if isinstance(safe_payload.get("case_context"), dict) else {}
    failure_reason = str(safe_payload.get("failure_reason") or "normal_generation_unavailable")
    chunks = _chunk_digest(safe_payload.get("chunks", []))

    system = """You are creating a DEMO-ONLY EBM candidate for a safety-gated RAG demo.
Rules:
1. Prefer the provided evidence chunks. Do not invent PMID, DOI, chunk_id, journal, or guideline metadata.
2. If the chunks do not directly support a clinical statement, write "lacking direct evidence" in that comment.
3. Every source must use only chunk_id values that appear in the provided chunks.
4. If contraindication or severe adverse event evidence appears, choose orange.
5. This output is not accepted until a separate verifier and deterministic gate approve it.
6. Return ONLY valid JSON. No markdown, no code fences.
7. Output schema:
{
  "light_color": "green|yellow|orange",
  "llmaaj_score": 0,
  "short_comment": "string",
  "rag_comments": [
    {
      "topic": "string",
      "comment": "string",
      "evidence_level": "Level_1|Level_2|Level_3|Level_4|Level_5|unknown",
      "grade": "Grade_A|Grade_B|Grade_C|unknown",
      "sources": [
        {"chunk_id": "chunk-id", "pmid": null, "doi": null, "six_s_level": "string", "ocebm_level": "string", "score": 0.0}
      ]
    }
  ],
  "alternatives": [],
  "warnings": []
}"""
    user_payload = {
        "failure_reason": failure_reason,
        "dx_summary": dx_summary,
        "case_context": case_context,
        "chunks": chunks
    }
    return [{"role": "user", "content": f"[SYSTEM]\n{system}\n\nPayload:\n{json.dumps(user_payload, ensure_ascii=False)}"}]


async def execute_synthetic_ebm_candidate(payload: dict) -> dict:
    conn = LLMModel.get_connection_for_task("synthetic_ebm_candidate")
    if not conn:
        return {
            "status": "unconfigured",
            "error": "synthetic_ebm_candidate has no ready verified LAVA binding",
            "ebm_hits": None
        }

    conn = dict(conn)
    adapter = get_adapter(conn.get("provider", ""))
    if not adapter:
        return {
            "status": "failed",
            "error": f"Unknown provider: {conn.get('provider')}",
            "ebm_hits": None
        }

    try:
        result = await adapter.chat(conn["api_key"], conn["model_id"], _build_prompt(payload), temperature=0.0, max_tokens=4096)
        ebm_hits = json.loads(_strip_json_fences(result.get("content", "")))
        if not isinstance(ebm_hits, dict):
            fallback = _deterministic_candidate(payload, "synthetic_ebm_candidate returned non-object JSON")
            if fallback:
                fallback["model"] = {"provider": "deterministic", "model_id": "retrieved_chunk_fallback"}
                fallback["demo_candidate_kind"] = "synthetic_ebm_candidate"
                return {"status": "ok", "error": None, "ebm_hits": fallback, "connection_id": None, "model": "retrieved_chunk_fallback"}
            return {"status": "failed", "error": "synthetic_ebm_candidate returned non-object JSON", "ebm_hits": None}
        ebm_hits["model"] = {"provider": conn["provider"], "model_id": conn["model_id"]}
        ebm_hits["demo_candidate_kind"] = "synthetic_ebm_candidate"
        return {
            "status": "ok",
            "error": None,
            "ebm_hits": ebm_hits,
            "connection_id": conn.get("id"),
            "model": conn.get("model_id")
        }
    except json.JSONDecodeError as e:
        fallback = _deterministic_candidate(payload, f"synthetic_ebm_candidate returned invalid JSON: {e}")
        if fallback:
            fallback["model"] = {"provider": "deterministic", "model_id": "retrieved_chunk_fallback"}
            fallback["demo_candidate_kind"] = "synthetic_ebm_candidate"
            return {"status": "ok", "error": None, "ebm_hits": fallback, "connection_id": None, "model": "retrieved_chunk_fallback"}
        return {
            "status": "failed",
            "error": f"synthetic_ebm_candidate returned invalid JSON: {e}",
            "ebm_hits": None
        }
    except Exception as e:
        safe_error = adapter.safe_error(e, conn.get("api_key", ""))
        fallback = _deterministic_candidate(payload, safe_error)
        if fallback:
            fallback["model"] = {"provider": "deterministic", "model_id": "retrieved_chunk_fallback"}
            fallback["demo_candidate_kind"] = "synthetic_ebm_candidate"
            return {"status": "ok", "error": None, "ebm_hits": fallback, "connection_id": None, "model": "retrieved_chunk_fallback"}
        return {
            "status": "failed",
            "error": safe_error,
            "ebm_hits": None
        }
