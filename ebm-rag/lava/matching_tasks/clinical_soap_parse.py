# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/clinical_soap_parse.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/clinical_soap_parse.py
# Timestamp: 2026-06-15 21:05 +08:00
# Version: v0.1
# Description:
#   Optional LAVA matching task for llmxx-server Phase 3 clinical SOAP parsing.
#   It converts local OCR SOAP fields into Dx/Tx/Hx/labs hints for RAG /check.
# Change Notes:
#   - v0.1: Added strict JSON-only LLM prompt, defensive normalization, and
#     conservative unconfigured/failed response contracts.
# Safety Notes:
#   - Does not invent missing clinical facts. Output is advisory only; the
#     llmxx-server deterministic mapper remains the fallback and final input
#     owner. This task stores no payloads, prompts, headers, or secrets.
# Verification Notes:
#   - Covered by py_compile, LAVA readiness/module checks, and llmxx-server
#     replay tests where unbound LAVA must not block intake.
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import re
from typing import Any

from lava.adapter import get_adapter
from lava.llm_model import LLMModel


_MAX_TEXT = 900
_MAX_LAB_KEYS = 16


def _strip_json_fences(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    parts = text.split("```")
    if len(parts) >= 3:
        candidate = parts[1].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
        return candidate
    return text.lstrip("`").lstrip("json").strip()


def _load_json_object(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    text = _strip_json_fences(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as first_error:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None, str(first_error)
        else:
            return None, str(first_error)
    if not isinstance(parsed, dict):
        return None, "LLM returned non-object JSON"
    return parsed, None


def _clean_text(value: Any, max_length: int = _MAX_TEXT) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length]


def _clean_list(value: Any, max_items: int = 10, max_length: int = 120) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    output: list[str] = []
    for item in raw_items:
        text = _clean_text(item, max_length=max_length)
        if text and text not in output:
            output.append(text)
        if len(output) >= max_items:
            break
    return output


def _safe_float(value: Any, default: float = 0.55) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, number)), 3)


def _safe_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 125:
        return None
    return number


def _safe_sex(value: Any) -> str | None:
    text = _clean_text(value, max_length=32).lower()
    aliases = {
        "m": "male",
        "male": "male",
        "man": "male",
        "男": "male",
        "f": "female",
        "female": "female",
        "woman": "female",
        "女": "female",
    }
    return aliases.get(text)


def _safe_labs(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    labs: dict[str, str] = {}
    for key, item in raw.items():
        safe_key = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", str(key or "").strip())[:40]
        safe_value = _clean_text(item, max_length=120)
        if safe_key and safe_value:
            labs[safe_key] = safe_value
        if len(labs) >= _MAX_LAB_KEYS:
            break
    return labs


def _safe_input_payload(payload: Any) -> dict[str, Any]:
    safe_payload = payload if isinstance(payload, dict) else {}
    soap = safe_payload.get("soap") if isinstance(safe_payload.get("soap"), dict) else {}
    vitals = safe_payload.get("vital_signs") if isinstance(safe_payload.get("vital_signs"), dict) else {}
    fallback = safe_payload.get("clinical_parse") if isinstance(safe_payload.get("clinical_parse"), dict) else {}
    return {
        "soap": {
            "S": _clean_text(soap.get("S"), 800),
            "O": _clean_text(soap.get("O"), 800),
            "A": _clean_text(soap.get("A"), 500),
            "P": _clean_text(soap.get("P"), 600),
        },
        "vital_signs": {
            "bp": _clean_text(vitals.get("bp"), 60),
            "hr": _clean_text(vitals.get("hr"), 60),
            "temp": _clean_text(vitals.get("temp"), 60),
            "rr": _clean_text(vitals.get("rr"), 60),
            "spo2": _clean_text(vitals.get("spo2"), 60),
        },
        "fallback_parse": {
            "dx": _clean_text(fallback.get("dx"), 500),
            "tx": _clean_text(fallback.get("tx"), 600),
            "hx": _clean_text(fallback.get("hx"), 900),
            "labs": _safe_labs(fallback.get("labs")),
            "age": _safe_int_or_none(fallback.get("age")),
            "sex": _safe_sex(fallback.get("sex")),
        },
    }


def _normalize_result(parsed: dict[str, Any], fallback_parse: dict[str, Any]) -> dict[str, Any]:
    dx = _clean_text(parsed.get("dx"), 500) or _clean_text(fallback_parse.get("dx"), 500)
    tx = _clean_text(parsed.get("tx"), 600) or _clean_text(fallback_parse.get("tx"), 600)
    hx = _clean_text(parsed.get("hx"), 900) or _clean_text(fallback_parse.get("hx"), 900)
    labs = _safe_labs(parsed.get("labs")) or _safe_labs(fallback_parse.get("labs"))
    age = _safe_int_or_none(parsed.get("age"))
    if age is None:
        age = _safe_int_or_none(fallback_parse.get("age"))
    sex = _safe_sex(parsed.get("sex")) or _safe_sex(fallback_parse.get("sex"))
    missing_fields = _clean_list(parsed.get("missing_fields"), max_items=8, max_length=80)
    uncertainty_flags = _clean_list(parsed.get("uncertainty_flags"), max_items=10, max_length=120)
    reason_codes = _clean_list(parsed.get("reason_codes"), max_items=10, max_length=80)
    if not dx and "dx_missing" not in missing_fields:
        missing_fields.append("dx_missing")
    if not tx and "tx_missing" not in missing_fields:
        missing_fields.append("tx_missing")
    if not hx and "hx_missing" not in missing_fields:
        missing_fields.append("hx_missing")

    return {
        "status": "ok",
        "dx": dx,
        "tx": tx,
        "hx": hx,
        "labs": labs,
        "age": age,
        "sex": sex,
        "parse_confidence": _safe_float(parsed.get("parse_confidence"), default=0.55),
        "missing_fields": missing_fields,
        "uncertainty_flags": uncertainty_flags,
        "reason_codes": reason_codes,
    }


def _build_prompt(safe_input: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "You parse clinical SOAP text for a RAG intake adapter. Return only valid JSON. "
        "Do not diagnose beyond the supplied A/P/S/O text. Do not add outside medical knowledge. "
        "If a field is absent, leave it empty and list it in missing_fields."
    )
    required_schema = {
        "dx": "assessment or diagnosis text copied/condensed from SOAP A",
        "tx": "plan or treatment text copied/condensed from SOAP P",
        "hx": "relevant subjective/objective history copied/condensed from SOAP S/O",
        "labs": {"safe_key": "vital/lab value from supplied text only"},
        "age": None,
        "sex": None,
        "parse_confidence": 0.0,
        "missing_fields": ["field_name"],
        "uncertainty_flags": ["short uncertainty reason"],
        "reason_codes": ["short_machine_code"],
    }
    user_payload = {
        "input": safe_input,
        "required_json_schema": required_schema,
    }
    content = f"[SYSTEM]\n{system}\n\nINPUT JSON:\n{json.dumps(user_payload, ensure_ascii=False)}"
    return [{"role": "user", "content": content}]


async def execute_clinical_soap_parse(payload: dict) -> dict:
    safe_input = _safe_input_payload(payload)
    fallback_parse = safe_input["fallback_parse"]
    conn = LLMModel.get_connection_for_task("clinical_soap_parse")
    if not conn:
        fallback = _normalize_result({}, fallback_parse)
        fallback.update({
            "status": "unconfigured",
            "error": "clinical_soap_parse has no ready verified LAVA binding",
            "model": None,
            "connection_id": None,
        })
        return fallback

    conn = dict(conn)
    adapter = get_adapter(conn.get("provider", ""))
    if not adapter:
        return {
            "status": "failed",
            "error": f"Unknown provider: {conn.get('provider')}",
            "dx": fallback_parse.get("dx", ""),
            "tx": fallback_parse.get("tx", ""),
            "hx": fallback_parse.get("hx", ""),
            "labs": fallback_parse.get("labs", {}),
            "age": fallback_parse.get("age"),
            "sex": fallback_parse.get("sex"),
            "parse_confidence": 0.0,
            "missing_fields": [],
            "uncertainty_flags": ["unknown_provider"],
            "reason_codes": ["unknown_provider"],
        }

    try:
        result = await adapter.chat(conn["api_key"], conn["model_id"], _build_prompt(safe_input), temperature=0.0, max_tokens=1600)
        parsed, parse_error = _load_json_object(result.get("content", ""))
        if parsed is None:
            fallback = _normalize_result({}, fallback_parse)
            fallback.update({
                "status": "failed",
                "error": f"clinical_soap_parse returned invalid JSON: {parse_error}",
                "model": {"provider": conn.get("provider"), "model_id": conn.get("model_id")},
                "connection_id": conn.get("id"),
            })
            return fallback
        normalized = _normalize_result(parsed, fallback_parse)
        normalized["model"] = {"provider": conn.get("provider"), "model_id": conn.get("model_id")}
        normalized["connection_id"] = conn.get("id")
        return normalized
    except Exception as exc:
        fallback = _normalize_result({}, fallback_parse)
        fallback.update({
            "status": "failed",
            "error": adapter.safe_error(exc, conn.get("api_key", "")),
            "model": {"provider": conn.get("provider"), "model_id": conn.get("model_id")},
            "connection_id": conn.get("id"),
        })
        return fallback
