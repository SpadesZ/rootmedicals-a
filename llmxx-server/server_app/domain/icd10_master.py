# 檔案路徑: rootmedicals-a/llmxx-server/server_app/domain/icd10_master.py
# 產生時間: 2026-06-18 11:32 +08:00
# 版本: v0.2
# 模組定位:
#   llmxx-server 的 ICD-10 master table helper。server 端需要用同一份 ICD seed 來補 diagnosis label、
#   normalized diagnosis，並支援 ICD-vs-A / ICD-vs-treatment gate。
# 主要責任:
#   1. 正規化 ICD-10 code 格式。
#   2. 從 rootmedicals-a/shared_data/icd10 載入 ICD master seed。
#   3. 提供 label、normalized diagnosis、aliases 給 clinical_mapper 與 response_builder 使用。
# 維護提醒:
#   - 這層只做 code/table lookup，不做醫師診斷對錯判定。
#   - 正式版換完整 ICD-10-CM master table 時，請保持 code/label/normalized_diagnosis/aliases 欄位。
#   - 若這裡讀不到 shared_data，ICD gate 會退化，醫師端可能出現缺 ICD 或無法確認 A 欄一致性的黃燈。
# 驗證方式:
#   - lookup_icd10("J30.9") 應回傳 allergic rhinitis seed。
#   - lookup_icd10("I48.91") 應回傳 atrial fibrillation seed。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..infra.settings import ROOTMEDICALS_A_DIR


@dataclass(frozen=True)
class ICD10Record:
    # 這個結構要和 ClinicalGuard client 的 ICD10Record 保持語意一致，讓 sidecar 與 server gate 對同一組欄位說話。
    code: str
    label: str
    normalized_diagnosis: str
    aliases: tuple[str, ...]

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.normalized_diagnosis, self.label, *self.aliases)))


def normalize_icd10_code(value: Any) -> str:
    raw = str(value or "").upper()
    raw = raw.replace(" ", "").replace("-", ".")
    # 處理實測常見 OCR/輸入混淆：I/1、O/0、J30/J3O。這不是疾病推論，只是格式修復。
    raw = raw.replace("O", "0").replace("I4B", "I48").replace("148", "I48").replace("J3O", "J30")
    match = re.search(r"([A-Z0-9])(\d{2})(?:[.:]?(\d{1,4}))?", raw)
    if not match:
        return ""
    first, second, decimal = match.groups()
    if first == "1" and second == "48":
        first = "I"
    if first == "0" and second == "11":
        first = "E"
    code = f"{first}{second}"
    if decimal:
        code = f"{code}.{decimal}"
    return code


def lookup_icd10(value: Any) -> ICD10Record | None:
    return _records_by_code().get(normalize_icd10_code(value))


def find_icd10_in_text(value: Any) -> ICD10Record | None:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return None
    record = lookup_icd10(text)
    if record:
        return record
    lowered = text.lower()
    for candidate in _records_by_code().values():
        # 文字搜尋只做明確包含，不用 fuzzy guessing，避免 server 替醫師自動改診斷分類。
        if candidate.label.lower() in lowered or candidate.normalized_diagnosis.lower() in lowered:
            return candidate
        if any(alias.lower() in lowered for alias in candidate.aliases):
            return candidate
    return None


def label_for_icd10(value: Any) -> str:
    record = lookup_icd10(value)
    return record.label if record else ""


def normalized_diagnosis_for_icd10(value: Any, fallback_label: str = "") -> str:
    record = lookup_icd10(value)
    if record:
        return record.normalized_diagnosis or record.label
    return " ".join(str(fallback_label or "").split()).strip()


def terms_for_icd10(value: Any, fallback_label: str = "") -> tuple[str, ...]:
    record = lookup_icd10(value)
    if record:
        return record.terms
    fallback = " ".join(str(fallback_label or "").split()).strip()
    return (fallback,) if fallback else tuple()


def _records_by_code() -> dict[str, ICD10Record]:
    global _CACHE
    try:
        return _CACHE
    except NameError:
        # ICD seed 讀取結果快取在 process 內；更新 shared_data 後需重啟 server 才會重新載入。
        _CACHE = _load_records()
        return _CACHE


def _load_records() -> dict[str, ICD10Record]:
    path = ROOTMEDICALS_A_DIR / "shared_data" / "icd10" / "icd10cm_master_demo.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # master table 缺失時保持 server 可啟動，final gate 會以較保守的黃燈/人工確認呈現。
        raw = []
    out: dict[str, ICD10Record] = {}
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, dict):
            continue
        code = normalize_icd10_code(row.get("code"))
        label = " ".join(str(row.get("label") or "").split()).strip()
        normalized = " ".join(str(row.get("normalized_diagnosis") or label).split()).strip()
        aliases_raw = row.get("aliases") if isinstance(row.get("aliases"), list) else []
        aliases = tuple(" ".join(str(alias).split()).strip() for alias in aliases_raw if str(alias or "").strip())
        if code and label:
            out[code] = ICD10Record(code=code, label=label, normalized_diagnosis=normalized, aliases=aliases)
    return out

