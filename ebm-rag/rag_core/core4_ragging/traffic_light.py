# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core4_ragging/traffic_light.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core4 查詢/驗證層，負責 retrieval、EBM 生成、ICD gate 與安全燈號。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core4_ragging/traffic_light.py
# Timestamp: 2026-06-16
# Version: v0.6
# Description: Core4 紅綠燈規則引擎（rule-based hard guard）。
#              explicit hard contraindications force orange; guideline cautions are preserved as warnings.
#              v0.6 adds conservative ICD-10 gate: missing/malformed ICD
#              downgrades to review, obvious ICD-vs-A mismatch forces orange.
# ----------------------------------------------------------------------------------------------------

from typing import Any

from rag_core.core4_ragging.icd_gate import evaluate_icd_gate

_CONTRAINDICATION_CAUTION_KEYWORDS = {
    "contraindicat",
    "bleeding risk",
    "risk of major bleeding",
    "active bleeding",
    "adverse effects",
    "adverse event"
}

_CONTRAINDICATION_HARD_KEYWORDS = {
    "fatal interaction",
    "lethal",
    "anaphylaxis",
    "black box",
    "do not use",
    "avoid in",
    "severe adverse",
    "prohibited"
}

_PATIENT_CONTEXT_HARD_KEYS = {
    "active_bleeding",
    "absolute_contraindication",
    "contraindication_present",
    "has_absolute_contraindication",
    "has_contraindication"
}

_PATIENT_CONTEXT_HARD_KEYWORDS = {
    "active bleeding",
    "major bleeding",
    "severe bleeding",
    "intracranial hemorrhage",
    "intracranial haemorrhage",
    "hemorrhagic stroke",
    "haemorrhagic stroke",
    "absolute contraindication",
    "contraindication to anticoagulation",
    "contraindicated for anticoagulation",
    "fatal interaction",
    "anaphylaxis"
}

_GREEN_SIX_S = {"System", "Summaries", "Syntheses"}
_GREEN_OCEBM = {"Level_1", "Level_2"}
_VALID_LIGHTS = {"green", "yellow", "orange"}


def _ensure_list(value) -> list:
    if isinstance(value, list):
        return value
    return []


def _normalize_text(value: Any) -> str:
    text = str(value or "").replace("-\n", "").replace("\n", " ")
    return " ".join(text.split()).lower()


def _chunk_text(chunk: dict) -> str:
    payload = chunk.get("payload")
    payload_text = payload.get("text") if isinstance(payload, dict) else None
    return _normalize_text(chunk.get("text") or payload_text or "")


def _chunk_value(chunk: dict, key: str) -> Any:
    value = chunk.get(key)
    if value not in (None, "", "unknown"):
        return value
    payload = chunk.get("payload")
    if isinstance(payload, dict):
        return payload.get(key)
    return None


def _chunk_has_contraindication_caution(chunk: dict) -> bool:
    text_lower = _chunk_text(chunk)
    flag = bool(_chunk_value(chunk, "has_contraindication_terms"))
    keyword_hit = any(keyword in text_lower for keyword in _CONTRAINDICATION_CAUTION_KEYWORDS)
    return flag or keyword_hit


def _chunk_has_explicit_hard_contraindication(chunk: dict) -> bool:
    text_lower = _chunk_text(chunk)
    return any(keyword in text_lower for keyword in _CONTRAINDICATION_HARD_KEYWORDS)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return False


def _context_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_context_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_context_values(item))
        return values
    if value in (None, "", [], {}):
        return []
    return [str(value)]


def _case_context_has_hard_contraindication(case_context: dict | None) -> bool:
    if not isinstance(case_context, dict):
        return False
    for key, value in case_context.items():
        if str(key).strip().lower() in _PATIENT_CONTEXT_HARD_KEYS and _as_bool(value):
            return True
    context_text = _normalize_text(" ".join(_context_values(case_context)))
    return any(keyword in context_text for keyword in _PATIENT_CONTEXT_HARD_KEYWORDS)


def contraindication_caution_reason(chunks: list) -> dict | None:
    safe_chunks = chunks if isinstance(chunks, list) else []
    for chunk in safe_chunks:
        if isinstance(chunk, dict) and _chunk_has_contraindication_caution(chunk):
            return {
                "chunk_id": chunk.get("chunk_id") or _chunk_value(chunk, "chunk_id"),
                "reason": "retrieved_evidence_mentions_contraindication_or_bleeding_risk"
            }
    return None


def contraindication_hard_gate_reason(chunks: list, case_context: dict | None = None) -> dict | None:
    safe_chunks = chunks if isinstance(chunks, list) else []
    for chunk in safe_chunks:
        if isinstance(chunk, dict) and _chunk_has_explicit_hard_contraindication(chunk):
            return {
                "chunk_id": chunk.get("chunk_id") or _chunk_value(chunk, "chunk_id"),
                "reason": "retrieved_evidence_contains_explicit_hard_contraindication"
            }
    if _case_context_has_hard_contraindication(case_context):
        caution = contraindication_caution_reason(safe_chunks) or {}
        return {
            "chunk_id": caution.get("chunk_id"),
            "reason": "patient_context_contains_hard_contraindication"
        }
    return None


def has_contraindication_caution(chunks: list) -> bool:
    return contraindication_caution_reason(chunks) is not None


def requires_contraindication_hard_gate(chunks: list, case_context: dict | None = None) -> bool:
    return contraindication_hard_gate_reason(chunks, case_context) is not None


def _has_strong_evidence(chunks: list) -> bool:
    for chunk in chunks:
        six_s_level = chunk.get("six_s_level")
        ocebm_level = chunk.get("ocebm_level")
        if six_s_level in _GREEN_SIX_S:
            return True
        if ocebm_level in _GREEN_OCEBM:
            return True
    return False


def _has_warning_code(warnings: list, code: str) -> bool:
    return any(isinstance(warning, dict) and warning.get("code") == code for warning in warnings)


def apply_traffic_light(chunks: list, ebm_result: dict, case_context: dict | None = None) -> dict:
    if not isinstance(ebm_result, dict):
        ebm_result = {}

    safe_chunks = chunks if isinstance(chunks, list) else []
    current_light = str(ebm_result.get("light_color", "yellow")).lower()
    if current_light not in _VALID_LIGHTS:
        current_light = "yellow"
    ebm_result["light_color"] = current_light
    ebm_result["warnings"] = _ensure_list(ebm_result.get("warnings"))

    icd_gate = evaluate_icd_gate(case_context)
    ebm_result["icd_gate"] = icd_gate
    if icd_gate.get("status") == "fail":
        ebm_result["light_color"] = "orange"
        if not _has_warning_code(ebm_result["warnings"], "icd_dx_mismatch"):
            ebm_result["warnings"].append({
                "code": "icd_dx_mismatch",
                "type": "icd_gate",
                "icd_code": icd_gate.get("code"),
                "expected_label": icd_gate.get("expected_label"),
                "message": "ICD-10 disease anchor conflicts with the assessment text; green display is blocked"
            })
        return ebm_result
    if icd_gate.get("status") == "review" and ebm_result["light_color"] == "green":
        ebm_result["light_color"] = "yellow"
        warning_code = str(icd_gate.get("reason") or "icd_review_required")
        if not _has_warning_code(ebm_result["warnings"], warning_code):
            ebm_result["warnings"].append({
                "code": warning_code,
                "type": "icd_gate",
                "icd_code": icd_gate.get("code"),
                "expected_label": icd_gate.get("expected_label"),
                "message": "ICD-10 anchor is missing or not confirmed by assessment text; physician review is required"
            })

    hard_gate = contraindication_hard_gate_reason(safe_chunks, case_context)
    if hard_gate is not None:
        ebm_result["light_color"] = "orange"
        if not _has_warning_code(ebm_result["warnings"], "contraindication_hard_gate"):
            ebm_result["warnings"].append({
                "code": "contraindication_hard_gate",
                "type": "contraindication",
                "chunk_id": hard_gate.get("chunk_id"),
                "reason": hard_gate.get("reason"),
                "message": "Hard contraindication signal detected; clinical output requires caution review"
            })
        return ebm_result

    caution = contraindication_caution_reason(safe_chunks)
    if caution is not None:
        if ebm_result["light_color"] == "green":
            ebm_result["light_color"] = "yellow"
        if not _has_warning_code(ebm_result["warnings"], "contraindication_caution_detected"):
            ebm_result["warnings"].append({
                "code": "contraindication_caution_detected",
                "type": "safety_caution",
                "chunk_id": caution.get("chunk_id"),
                "reason": caution.get("reason"),
                "message": "Retrieved evidence discusses contraindications or bleeding-risk precautions; review patient-specific risk before treatment decisions"
            })

    has_strong = _has_strong_evidence(safe_chunks)
    if ebm_result["light_color"] == "green" and not has_strong:
        ebm_result["light_color"] = "yellow"
        ebm_result["warnings"].append({
            "code": "low_evidence_downgrade",
            "type": "low_evidence_downgrade",
            "message": "Green light downgraded because retrieved evidence lacks high-level support"
        })
    elif ebm_result["light_color"] not in {"orange", "yellow"} and has_strong:
        ebm_result["light_color"] = "green"

    return ebm_result
