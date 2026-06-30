# 檔案路徑: rootmedicals-a/llmxx-client/apps/local-ocr/src/llmxx_client_local_ocr/vital_signs.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: LocalOCR 客戶端程式，負責截圖、OCR/薄客戶端傳送、醫師端提醒視窗與驗證工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# Path: ./llmxx-client-local-ocr/src/llmxx_client_local_ocr/vital_signs.py
# Version History:
# v0.1 20260614-0000 - Add OCR cleanup for numeric Vital Signs fields before server payload output.
# v0.2 20260614-0000 - Reconstruct common BP slash loss from OCR digit runs.

from __future__ import annotations

import re
from typing import Dict


VITAL_FIELDS = ("bp", "hr", "temp", "rr", "spo2")


def normalize_vital_signs(raw: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for field in VITAL_FIELDS:
        out[field] = normalize_vital_text(field, str(raw.get(field, "") or ""))
    return out


def normalize_vital_text(field_name: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    # Inner v0.1: Vital Signs boxes should contain numeric values only, so these OCR corrections
    # are intentionally narrower than the SOAP clinical-text normalizer.
    translation = str.maketrans({
        "O": "0",
        "o": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "g": "9",
    })
    text = text.translate(translation)
    text = text.replace("／", "/").replace("\\", "/").replace(",", ".")

    if field_name == "bp":
        cleaned = re.sub(r"[^0-9/]", "", text)
        match = re.search(r"(\d{2,3})/(\d{2,3})", cleaned)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
        digits = re.sub(r"\D", "", cleaned)
        # Inner v0.2: EasyOCR sometimes reads the BP slash as nothing or as "1".
        # For demo-HIS numeric BP fields, reconstruct systolic/diastolic from digit runs.
        if len(digits) in (5, 6):
            return f"{digits[:3]}/{digits[-2:]}"
        if len(digits) == 4:
            return f"{digits[:2]}/{digits[2:]}"
        return cleaned

    if field_name == "temp":
        cleaned = re.sub(r"[^0-9.]", "", text)
        match = re.search(r"\d{2}(?:\.\d)?", cleaned)
        return match.group(0) if match else cleaned

    cleaned = re.sub(r"[^0-9]", "", text)
    return cleaned
