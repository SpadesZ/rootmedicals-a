# 檔案路徑: rootmedicals-a/legacy/client-local-ocr-legacy/src/llmxx_client_local_ocr_legacy/normalizer.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: LocalOCR 客戶端程式，負責截圖、OCR/薄客戶端傳送、醫師端提醒視窗與驗證工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: rootmedicals-a/llmxx-client/apps/local-ocr/src/llmxx_client_local_ocr/normalizer.py
# Timestamp: 2026-06-16 16:08 +08:00
# Version: v0.4
# Description:
#   Configurable OCR text normalizer for LocalOCR SOAP fields. Applies explicit
#   demo-safe medical replacements, conservative lexicon repair, and auditable
#   spacing fixes before PHI redaction and server payload creation.
# Version History:
# v0.1 20260614-0000 - Add configurable OCR text normalization with auditable correction events.
# v0.2 20260614-0000 - Normalize case-only OCR differences and keep replacements auditable.
# v0.3 20260614-0000 - Preserve configured term casing and avoid over-merging unrelated word pairs.
# v0.4 20260616-1608 - Repair nasal-congestion OCR spacing when a following duration digit was joined.

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple


class LexiconNormalizer:
    def __init__(self, normalizer_config: Dict[str, Any]):
        self.enabled = bool(normalizer_config.get("enabled", True))
        self.min_similarity = float(normalizer_config.get("min_similarity", 0.88) or 0.88)
        self.max_joined_tokens = int(normalizer_config.get("max_joined_tokens", 3) or 3)
        self.known_terms = [str(item).strip() for item in normalizer_config.get("known_terms", []) if str(item).strip()]
        self.literal_replacements = {
            str(src).strip(): str(dst).strip()
            for src, dst in dict(normalizer_config.get("literal_replacements", {})).items()
            if str(src).strip() and str(dst).strip()
        }

    def normalize_soap(self, soap: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
        if not self.enabled:
            return dict(soap), {field_name: [] for field_name in ("S", "O", "A", "P")}

        normalized: Dict[str, str] = {}
        events: Dict[str, List[Dict[str, Any]]] = {}
        for field_name in ("S", "O", "A", "P"):
            text, field_events = self.normalize_text(str(soap.get(field_name, "") or ""))
            normalized[field_name] = text
            events[field_name] = field_events
        return normalized, events

    def normalize_text(self, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        # Inner v0.1: run explicit known OCR replacements first; these are safest because each
        # replacement is visible in config and recorded in payload normalizations.
        out = str(text or "")
        events: List[Dict[str, Any]] = []
        for source, target in self.literal_replacements.items():
            pattern = re.compile(rf"(?<![A-Za-z]){re.escape(source)}(?![A-Za-z])", re.IGNORECASE)

            def replace_literal(match: re.Match[str]) -> str:
                events.append(
                    {
                        "source": match.group(0),
                        "target": target,
                        "method": "literal",
                        "score": 1.0,
                    }
                )
                return target

            out = pattern.sub(replace_literal, out)

        if self.known_terms:
            out, fuzzy_events = self._normalize_known_terms(out)
            events.extend(fuzzy_events)
        out, spacing_events = self._repair_clinical_spacing(out)
        events.extend(spacing_events)
        return out, events

    def _repair_clinical_spacing(self, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        events: List[Dict[str, Any]] = []
        pattern = re.compile(r"\b(nasal\s+congestion)(?=\d)", re.IGNORECASE)

        def repair(match: re.Match[str]) -> str:
            source = match.group(0)
            target = f"{source} "
            events.append(
                {
                    "source": source,
                    "target": target,
                    "method": "clinical_spacing",
                    "score": 1.0,
                }
            )
            return target

        return pattern.sub(repair, text), events

    def _normalize_known_terms(self, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        tokens = list(re.finditer(r"[A-Za-z]+", text))
        if not tokens:
            return text, []

        replacements: List[Tuple[int, int, str, Dict[str, Any]]] = []
        index = 0
        while index < len(tokens):
            best: Tuple[float, int, str] | None = None
            max_span = min(self.max_joined_tokens, len(tokens) - index)
            for span_len in range(max_span, 0, -1):
                span = tokens[index:index + span_len]
                source_text = text[span[0].start():span[-1].end()]
                compact_source = re.sub(r"[^A-Za-z]+", "", source_text).lower()
                if len(compact_source) < 4:
                    continue
                for term in self.known_terms:
                    compact_term = re.sub(r"[^A-Za-z]+", "", term).lower()
                    if not compact_term or abs(len(compact_source) - len(compact_term)) > 3:
                        continue
                    if span_len > 1 and " " not in term and abs(len(compact_source) - len(compact_term)) > 1:
                        continue
                    score = SequenceMatcher(None, compact_source, compact_term).ratio()
                    if score >= self.min_similarity and (best is None or score > best[0]):
                        best = (score, span_len, term)
            if best is None:
                index += 1
                continue

            score, span_len, term = best
            span = tokens[index:index + span_len]
            source_text = text[span[0].start():span[-1].end()]
            if source_text != term:
                replacements.append(
                    (
                        span[0].start(),
                        span[-1].end(),
                        term,
                        {
                            "source": source_text,
                            "target": term,
                            "method": "lexicon_similarity",
                            "score": round(score, 4),
                        },
                    )
                )
            index += span_len

        if not replacements:
            return text, []

        events: List[Dict[str, Any]] = []
        out_parts: List[str] = []
        cursor = 0
        for start, end, replacement, event in replacements:
            out_parts.append(text[cursor:start])
            out_parts.append(replacement)
            events.append(event)
            cursor = end
        out_parts.append(text[cursor:])
        return "".join(out_parts), events


def _match_case(target: str, source: str) -> str:
    if source.isupper():
        return target.upper()
    if source[:1].isupper() and source[1:].islower():
        return target.capitalize()
    return target
