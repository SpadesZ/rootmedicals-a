# 檔案路徑: rootmedicals-a/llmxx-server/server_app/core/clinical_mapper.py
# 產生時間: 2026-06-18 11:22 +08:00
# 版本: v0.2
# 模組定位:
#   llmxx-server 的 clinical parse mapper。它把正式 client payload 轉成 RAG、LAVA 與 final gate
#   共用的 ClinicalParse，屬於 deterministic normalization，不是 LLM 推論。
# 主要責任:
#   1. 清理 SOAP A/P/S/O 與 vital signs 的長度與空白。
#   2. 將 ICD-10 code、diagnosis label、normalized diagnosis 整理成同一個臨床 anchor。
#   3. 產生 missing_fields、uncertainty_flags 與去識別 trace hash，供後續判斷與除錯。
# 維護提醒:
#   - 這裡不可把 S/O 內容拿來「猜」ICD；ICD 只能來自 client 結構化欄位或 master table 正規化。
#   - 若 LAVA clinical_soap_parse 有結果，api/main.py 會以 additive 方式合併，不應破壞這裡的 fallback。
# 驗證方式:
#   - py_compile。
#   - 用含/不含 ICD 的 formal payload 跑 /api/intake，確認黃/橘/綠原因碼能區分。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..domain.icd10_master import label_for_icd10, normalize_icd10_code, normalized_diagnosis_for_icd10
from ..contracts.schemas import ClinicalParse, FormalClientPayload


def _clean_text(value: str, max_length: int = 700) -> str:
    # 上游可能來自 OCR、JSON 或人工輸入；先壓成單行，避免 RAG query 被多餘換行干擾。
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length]


def _field_hash(value: str) -> str:
    if not value:
        return ""
    # trace 只存 hash，不存原文，讓工程除錯可確認欄位是否變動，又不在 server 狀態表保存病歷文字。
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def map_payload_to_clinical(payload: FormalClientPayload) -> ClinicalParse:
    soap = payload.soap
    vitals = payload.vital_signs
    dx_text = _clean_text(payload.dx_text or soap.A, 260)
    tx = _clean_text(soap.P, 360)
    hx_parts = []
    if soap.S:
        hx_parts.append(f"S: {_clean_text(soap.S, 360)}")
    if soap.O:
        hx_parts.append(f"O: {_clean_text(soap.O, 360)}")
    hx = "; ".join(hx_parts)
    labs: dict[str, Any] = {
        "bp": _clean_text(vitals.bp, 40),
        "hr": _clean_text(vitals.hr, 40),
        "temp": _clean_text(vitals.temp, 40),
        "rr": _clean_text(vitals.rr, 40),
        "spo2": _clean_text(vitals.spo2, 40),
    }
    labs = {key: value for key, value in labs.items() if value}
    icd_code = normalize_icd10_code(payload.icd10_code or payload.icd_code)
    diagnosis_label = _clean_text(payload.diagnosis_label or label_for_icd10(icd_code), 260)
    normalized_diagnosis = _clean_text(payload.normalized_diagnosis or normalized_diagnosis_for_icd10(icd_code, diagnosis_label), 260)
    # RAG 查詢優先使用 normalized diagnosis / ICD label；A 欄 dx_text 保留為醫師實際 assessment，
    # 後續 final gate 會檢查 ICD 與 A 是否明顯不一致。
    dx = normalized_diagnosis or diagnosis_label or dx_text
    missing = []
    if not dx_text and not dx:
        missing.append("soap.A")
    if not tx:
        missing.append("soap.P")
    if not hx:
        missing.append("soap.S_or_O")
    if not icd_code:
        missing.append("icd10_code")
    uncertainty = []
    if len(dx) < 3:
        uncertainty.append("dx_too_short")
    if len(tx) < 3:
        uncertainty.append("tx_too_short")
    filled = 4 - min(len(missing), 4)
    parse_confidence = max(0.1, min(0.92, 0.25 + filled * 0.17 + (0.08 if labs else 0.0)))
    return ClinicalParse(
        dx=dx,
        tx=tx,
        hx=hx,
        icd_code=icd_code,
        icd10_code=icd_code,
        icd_label=diagnosis_label or icd_code,
        diagnosis_label=diagnosis_label,
        normalized_diagnosis=normalized_diagnosis,
        dx_text=dx_text,
        labs=labs,
        parse_confidence=round(parse_confidence, 2),
        missing_fields=missing,
        uncertainty_flags=uncertainty,
        llm_status="deterministic_fallback",
        trace={
            "S": f"hash:{_field_hash(soap.S)}" if soap.S else "",
            "O": f"hash:{_field_hash(soap.O)}" if soap.O else "",
            "A": f"hash:{_field_hash(soap.A)}" if soap.A else "",
            "P": f"hash:{_field_hash(soap.P)}" if soap.P else "",
        },
    )

