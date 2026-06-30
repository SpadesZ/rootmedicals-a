# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core4_ragging/icd_gate.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core4 查詢/驗證層，負責 retrieval、EBM 生成、ICD gate 與安全燈號。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core4_ragging/icd_gate.py
# Timestamp: 2026-06-17 14:55 +08:00
# Version: v0.3
# Description:
#   Conservative ICD-10 disease-classification gate for Core4 traffic light.
#   ICD is treated as the physician's disease anchor: missing/malformed ICD
#   requires review, and only obvious ICD-vs-assessment mismatch forces orange.
#   v0.2 compares ICD against dx_text/A-field while allowing diagnosis_label
#   to represent the normalized ICD master diagnosis for RAG retrieval.
#   v0.3 accepts structured ICD master diagnosis / normalized diagnosis as
#   confirmation when OCR dx_text is noisy, while still forcing orange for
#   obvious known-disease conflicts in the A-field.
# ----------------------------------------------------------------------------------------------------

import re
from typing import Any


ICD_DISEASE_PATTERNS = {
    "J30": {"label": "allergic rhinitis", "terms": {"allergic rhinitis", "rhinitis", "hay fever", "nasal allergy"}},
    "I48": {"label": "atrial fibrillation", "terms": {"atrial fibrillation", "afib", "a fib", "af ", "af,", "af."}},
    "E11": {"label": "type 2 diabetes mellitus", "terms": {"type 2 diabetes", "diabetes mellitus", "dm2", "t2dm"}},
    "G43": {"label": "migraine", "terms": {"migraine"}},
    "J20": {"label": "acute bronchitis", "terms": {"acute bronchitis", "bronchitis"}},
    "K52": {"label": "gastroenteritis", "terms": {"gastroenteritis", "colitis"}},
}


def normalize_icd_code(value: Any) -> str:
    raw = str(value or "").upper().replace(" ", "")
    raw = raw.replace("O", "0").replace("I4B", "I48").replace("148", "I48").replace("J3O", "J30")
    match = re.search(r"([A-Z0-9])(\d{2})(?:[.\-:]?(\d{1,3}))?", raw)
    if not match:
        return ""
    first, second, decimal = match.groups()
    if first == "1" and second == "48":
        first = "I"
    code = f"{first}{second}"
    if decimal:
        code = f"{code}.{decimal}"
    return code


def icd_family(value: Any) -> str:
    code = normalize_icd_code(value)
    return code[:3] if len(code) >= 3 else ""


def evaluate_icd_gate(case_context: dict | None) -> dict:
    context = case_context if isinstance(case_context, dict) else {}
    code = normalize_icd_code(context.get("icd10_code") or context.get("icd_code"))
    dx_text = str(context.get("dx_text") or context.get("assessment_text") or "").strip()
    dx = str(context.get("dx") or "").strip()
    diagnosis_label = str(context.get("diagnosis_label") or context.get("normalized_diagnosis") or "").strip()
    normalized_diagnosis = str(context.get("normalized_diagnosis") or "").strip()
    result = {
        "status": "pass",
        "code": code,
        "family": icd_family(code),
        "expected_label": "",
        "reason": "",
    }
    if not code:
        result["status"] = "review"
        result["reason"] = "icd_missing"
        return result

    family = icd_family(code)
    pattern = ICD_DISEASE_PATTERNS.get(family)
    if not pattern:
        result["expected_label"] = diagnosis_label
        if diagnosis_label and diagnosis_label.lower() in f" {dx.lower()} ":
            return result
        result["status"] = "review"
        result["reason"] = "icd_unknown_family"
        return result

    result["expected_label"] = diagnosis_label or str(pattern.get("label") or "")
    dx_lower = f" {dx_text.lower()} "
    structured_lower = f" {dx.lower()} {diagnosis_label.lower()} {normalized_diagnosis.lower()} "
    expected_terms = pattern.get("terms") if isinstance(pattern.get("terms"), set) else set()
    if diagnosis_label:
        expected_terms = set(expected_terms)
        expected_terms.add(diagnosis_label)

    for other_family, other_pattern in ICD_DISEASE_PATTERNS.items():
        if other_family == family:
            continue
        other_terms = other_pattern.get("terms") if isinstance(other_pattern.get("terms"), set) else set()
        if any(str(term).lower() in dx_lower for term in other_terms):
            result["status"] = "fail"
            result["reason"] = "icd_dx_mismatch"
            return result

    if any(str(term).lower() in structured_lower for term in expected_terms):
        return result
    if any(str(term).lower() in dx_lower for term in expected_terms):
        return result

    result["status"] = "review"
    result["reason"] = "icd_dx_not_confirmed_by_assessment_text"
    return result
