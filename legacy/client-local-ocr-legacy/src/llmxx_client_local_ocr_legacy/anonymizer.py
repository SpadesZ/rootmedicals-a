# 檔案路徑: rootmedicals-a/llmxx-client/apps/local-ocr/src/llmxx_client_local_ocr/anonymizer.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: LocalOCR 客戶端程式，負責截圖、OCR/薄客戶端傳送、醫師端提醒視窗與驗證工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# Path: ./llmxx-client-local-ocr/src/llmxx_client_local_ocr/anonymizer.py
# Version History:
# v0.1 20260614-0000 - Initial Regex PHI masking engine for OCR strings.
# v0.2 20260614-0000 - Fix labeled Chinese patient-name redaction to avoid leaking the last character.
# v0.3 20260614-0000 - Make labeled medical-record redaction tolerant of OCR symbol mistakes.

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .models import RedactionEvent, SanitizedSOAP


class RegexAnonymizer:
    def __init__(self, anonymizer_config: Dict):
        self.replacement = str(anonymizer_config.get("replacement", "[REDACTED]"))
        self.strict_chinese_name = bool(anonymizer_config.get("redact_unlabeled_chinese_name_candidates", False))
        self.patterns = self._build_patterns()

    def sanitize_soap(self, soap: Dict[str, str]) -> SanitizedSOAP:
        sanitized: Dict[str, str] = {}
        redactions: Dict[str, List[RedactionEvent]] = {}
        for field_name in ("S", "O", "A", "P"):
            text, events = self.sanitize_text(str(soap.get(field_name, "") or ""))
            sanitized[field_name] = text
            redactions[field_name] = events
        return SanitizedSOAP(soap=sanitized, redactions=redactions)

    def sanitize_text(self, text: str) -> Tuple[str, List[RedactionEvent]]:
        out = str(text or "")
        events: List[RedactionEvent] = []
        for pattern_name, pattern in self.patterns:
            out, count = pattern.subn(self.replacement, out)
            if count:
                events.append(RedactionEvent(pattern_name=pattern_name, count=count))
        return out, events

    def _build_patterns(self) -> List[Tuple[str, re.Pattern[str]]]:
        patterns: List[Tuple[str, re.Pattern[str]]] = [
            ("taiwan_id", re.compile(r"\b[A-Z][12]\d{8}\b", re.IGNORECASE)),
            (
                "date_yyyy_mm_dd",
                re.compile(r"\b(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:日)?\b"),
            ),
            ("date_roc", re.compile(r"\b(?:民國)?\d{2,3}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:日)?\b")),
            (
                "medical_record_labeled",
                re.compile(
                    r"(?:病歷號|病歷|病號|MRN|Medical\s*Record|Chart\s*No\.?)\s*[:：#]?\s*[\S]{3,}",
                    re.IGNORECASE,
                ),
            ),
            (
                "name_labeled_zh",
                re.compile(
                    r"(?:病患姓名|患者姓名|姓名|病患|患者|Patient\s*Name|Name)\s*[:：]?\s*[\u4e00-\u9fff]{2,4}",
                    re.IGNORECASE,
                ),
            ),
        ]
        if self.strict_chinese_name:
            # Inner v0.1: Kept configurable because unlabeled 2-4 CJK words can also be valid clinical terms.
            patterns.append(("name_unlabeled_zh_candidate", re.compile(r"(?<![\u4e00-\u9fff])[\u4e00-\u9fff]{2,4}(?![\u4e00-\u9fff])")))
        return patterns
