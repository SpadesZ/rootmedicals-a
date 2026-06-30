# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/src/clinical_guard_app/icd10_master.py
# 產生時間: 2026-06-18 10:25 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard ICD-10 master table loader。醫師端畫面不能用普通 dropdown 硬塞少量 demo code，
#   因為正式 ICD-10-CM 是數萬筆等級；因此 UI 面只依賴這個 searchable master table 介面。
# 主要責任:
#   1. 從 shared_data/icd10/icd10cm_master_demo.json 載入目前 demo 可用的 ICD seed。
#   2. 提供 code、display text、疾病名稱與 aliases 的 autocomplete search。
#   3. 將常見 OCR/輸入混淆轉回 canonical ICD-10 格式，例如 I48.91、J30.9。
# 維護提醒:
#   - 未來換成完整 ICD-10-CM master table 時，請保持 ICD10Record 欄位不變，UI 與 server 才不用重寫。
#   - 這層只做資料表查找與格式正規化，不判斷 A/P 是否合理；臨床一致性由 llmxx-server final gate 負責。
#   - 本檔不保存病患資料、不連網，適合放在 client 端協助輸入。
# 驗證方式:
#   - py_compile。
#   - 手動輸入 code、疾病名稱、alias，確認 autocomplete 與 selected display 一致。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ICD10Record:
    # 維護筆記：這個 dataclass 是 client/server 共用語意的最小交集。正式 master table 可增加來源欄位，
    # 但不要移除 code、label、normalized_diagnosis，否則 sidecar 與 RAG query priority 會失去依據。
    code: str
    label: str
    normalized_diagnosis: str
    aliases: tuple[str, ...]
    source: str = ""
    year: int = 0
    billable: bool = True

    @property
    def display(self) -> str:
        return f"{self.code} - {self.label}"

    @property
    def search_text(self) -> str:
        # search_text 預先把 code、正式名稱、normalized diagnosis、alias 合併，讓 autocomplete 保持簡單。
        # 目前資料量很小可直接 substring；正式版若載入 74,000+ 筆，這裡應改成索引或 SQLite FTS。
        return " ".join([self.code, self.label, self.normalized_diagnosis, *self.aliases]).lower()


class ICD10MasterTable:
    def __init__(self, records: list[ICD10Record]):
        self.records = records
        self._by_code = {record.code.upper(): record for record in records}
        self._by_display = {record.display.lower(): record for record in records}

    @classmethod
    def load_default(cls) -> "ICD10MasterTable":
        data_path = _default_master_path()
        records = _load_records(data_path)
        return cls(records)

    def lookup_code(self, value: str) -> ICD10Record | None:
        code = normalize_icd10_code(value)
        if not code:
            return None
        return self._by_code.get(code)

    def parse_display(self, value: str) -> ICD10Record | None:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return None
        direct = self._by_display.get(text.lower())
        if direct:
            return direct
        code_match = self.lookup_code(text)
        if code_match:
            return code_match
        lowered = text.lower()
        for record in self.records:
            if lowered == record.normalized_diagnosis.lower() or lowered == record.label.lower():
                return record
        return None

    def search(self, query: str, limit: int = 12) -> list[ICD10Record]:
        needle = " ".join(str(query or "").split()).strip().lower()
        if not needle:
            return self.records[:limit]
        code_needle = normalize_icd10_code(needle)
        scored: list[tuple[int, ICD10Record]] = []
        for record in self.records:
            # 分數只用於 UI 排序，不代表臨床置信度。final gate 不能把這個 autocomplete score 當成醫學判斷。
            score = _match_score(record, needle, code_needle)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].code))
        return [record for _, record in scored[:limit]]


def normalize_icd10_code(value: Any) -> str:
    raw = str(value or "").upper()
    raw = raw.replace(" ", "").replace("-", ".")
    # 這些替換是針對實測 OCR/輸入常見錯字：I/1、O/0、J30/J3O。只處理格式，不推測疾病。
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


def _match_score(record: ICD10Record, needle: str, code_needle: str) -> int:
    code = record.code.lower()
    label = record.label.lower()
    normalized = record.normalized_diagnosis.lower()
    if code_needle and record.code.upper().startswith(code_needle):
        return 100
    if needle == code:
        return 100
    if label.startswith(needle) or normalized.startswith(needle):
        return 80
    if needle in record.search_text:
        return 50
    tokens = [token for token in needle.split() if len(token) >= 2]
    if tokens and all(token in record.search_text for token in tokens):
        return 40
    return 0


def _default_master_path() -> Path:
    root = Path(__file__).resolve().parents[5]
    return root / "shared_data" / "icd10" / "icd10cm_master_demo.json"


def _load_records(path: Path) -> list[ICD10Record]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # UI 不應因 ICD seed 檔遺失而無法啟動；空表會讓醫師端顯示無建議，server 端則走 ICD 缺失黃燈。
        raw = []
    records: list[ICD10Record] = []
    if not isinstance(raw, list):
        return records
    for row in raw:
        if not isinstance(row, dict):
            continue
        code = normalize_icd10_code(row.get("code"))
        label = " ".join(str(row.get("label") or "").split()).strip()
        normalized = " ".join(str(row.get("normalized_diagnosis") or label).split()).strip()
        aliases_raw = row.get("aliases") if isinstance(row.get("aliases"), list) else []
        aliases = tuple(" ".join(str(alias).split()).strip() for alias in aliases_raw if str(alias or "").strip())
        if not code or not label:
            continue
        records.append(
            ICD10Record(
                code=code,
                label=label,
                normalized_diagnosis=normalized,
                aliases=aliases,
                source=str(row.get("source") or ""),
                year=int(row.get("year") or 0),
                billable=bool(row.get("billable", True)),
            )
        )
    return records
