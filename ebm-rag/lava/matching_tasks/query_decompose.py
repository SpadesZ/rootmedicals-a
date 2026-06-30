# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/query_decompose.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/query_decompose.py
# Timestamp: 2026-06-16
# Version: v0.2
# Description: Optional LAVA matching task for Core4 query decomposition.
#              The output is strictly validated before Core4 can use it.
# Change Notes:
#              - v0.2: Expose a reusable task-id helper so rag_query_strategy can
#                use the same strict validation with its own LAVA binding.
# ----------------------------------------------------------------------------------------------------

import json
import re
from typing import Any

from lava.adapter import get_adapter
from lava.llm_model import LLMModel

_MAX_QUERY_COUNT = 4
_MIN_QUERY_LENGTH = 8
_MAX_QUERY_LENGTH = 220
_ACCEPTED_INTENTS = {"guideline", "efficacy", "contraindication", "alternatives", "diagnosis", "safety", "evidence"}
_ANCHOR_STOPWORDS = {
    "with",
    "that",
    "this",
    "from",
    "treatment",
    "therapy",
    "patient",
    "patients",
    "clinical",
    "guideline",
    "guidelines",
    "diagnosis",
    "diagnostic",
    "management",
    "alternative",
    "alternatives",
    "contraindication",
    "contraindications",
    "adverse",
    "effects",
    "efficacy",
    "evidence",
    "standard",
    "criteria",
}


def _strip_json_fence(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text.startswith("```"):
        return text
    parts = text.split("```")
    if len(parts) >= 3:
        candidate = parts[1].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
        return candidate
    return text.lstrip("`").lstrip("json").strip()


def _load_json_object(raw_text: str) -> tuple[dict | None, str | None]:
    text = _strip_json_fence(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start:end + 1]
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                return None, str(error)
        else:
            return None, str(error)
    if not isinstance(parsed, dict):
        return None, "LLM query decomposition returned non-object JSON"
    return parsed, None


def _clean_text(value: Any, max_length: int = 1800) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length]


def _safe_context(case_context: Any) -> dict:
    if not isinstance(case_context, dict):
        return {}
    safe = {}
    for key in ["dx", "tx", "hx", "age", "sex"]:
        value = case_context.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            safe[key] = _clean_text(value, max_length=400)
    return safe


def _anchor_terms(dx_summary: str, case_context: dict) -> set[str]:
    raw_parts = [dx_summary, case_context.get("dx", ""), case_context.get("tx", "")]
    raw_text = " ".join(str(part or "") for part in raw_parts).lower()
    tokens = re.findall(r"[0-9a-zA-Z_\-\u4e00-\u9fff]+", raw_text)
    anchors = set()
    for token in tokens:
        token = token.strip("_-")
        if len(token) < 4 and not re.search(r"[\u4e00-\u9fff]{2,}", token):
            continue
        if token in _ANCHOR_STOPWORDS:
            continue
        anchors.add(token)
    return anchors


def _query_has_anchor(query: str, anchors: set[str]) -> bool:
    if not anchors:
        return True
    lowered = query.lower()
    return any(anchor in lowered for anchor in anchors)


def _normalize_for_dedupe(query: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", query.lower()).strip()


def _sanitize_query(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    query = re.sub(r"\s+", " ", value).strip()
    if len(query) < _MIN_QUERY_LENGTH or len(query) > _MAX_QUERY_LENGTH:
        return None
    if "{" in query or "}" in query or "[" in query or "]" in query:
        return None
    return query


def _validated_queries(raw_items: Any, base_queries: list[str], anchors: set[str]) -> tuple[list[dict], list[str]]:
    if not isinstance(raw_items, list):
        return [], ["queries must be a list"]

    accepted: list[dict] = []
    errors: list[str] = []
    seen = {_normalize_for_dedupe(query) for query in base_queries if isinstance(query, str)}

    for index, item in enumerate(raw_items):
        if isinstance(item, dict):
            intent = str(item.get("intent") or "evidence").strip().lower()
            raw_query = item.get("query")
        else:
            intent = "evidence"
            raw_query = item

        if intent not in _ACCEPTED_INTENTS:
            errors.append(f"queries[{index}].intent is invalid")
            continue

        query = _sanitize_query(raw_query)
        if query is None:
            errors.append(f"queries[{index}].query is invalid")
            continue

        if not _query_has_anchor(query, anchors):
            errors.append(f"queries[{index}].query has no dx or tx anchor")
            continue

        normalized = _normalize_for_dedupe(query)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        accepted.append({"intent": intent, "query": query})
        if len(accepted) >= _MAX_QUERY_COUNT:
            break

    return accepted, errors


def _loose_query_items(raw_text: str, base_queries: list[str], anchors: set[str]) -> tuple[list[dict], list[str]]:
    candidates: list[dict] = []
    pair_pattern = re.compile(
        r'"intent"\s*:\s*"(?P<intent>[^"]+)"\s*,\s*"query"\s*:\s*"(?P<query>[^"]+)"',
        re.IGNORECASE
    )
    for match in pair_pattern.finditer(raw_text):
        candidates.append({
            "intent": match.group("intent"),
            "query": match.group("query")
        })

    if not candidates:
        for line in raw_text.splitlines():
            if '"query"' not in line and "'query'" not in line:
                continue
            if ":" not in line:
                continue
            value = line.split(":", 1)[1].strip().strip(",").strip().strip('"').strip("'")
            if value:
                candidates.append({"intent": "evidence", "query": value})

    return _validated_queries(candidates, base_queries, anchors)


async def _execute_query_decompose_for_task(task_id: str, payload: dict) -> dict:
    safe_task_id = str(task_id or "query_decompose").strip() or "query_decompose"
    conn = LLMModel.get_connection_for_task(safe_task_id)
    if not conn:
        return {
            "status": "unconfigured",
            "queries": [],
            "items": [],
            "model": None,
            "connection_id": None,
            "error": f"{safe_task_id} has no ready verified LAVA binding",
        }

    if not isinstance(payload, dict):
        return {
            "status": "failed",
            "queries": [],
            "items": [],
            "model": None,
            "connection_id": None,
            "error": f"{safe_task_id} payload must be an object",
        }

    conn = dict(conn)
    adapter = get_adapter(conn["provider"])
    if not adapter:
        return {
            "status": "failed",
            "queries": [],
            "items": [],
            "model": conn.get("model_id"),
            "connection_id": conn.get("id"),
            "error": f"Unknown provider: {conn.get('provider')}",
        }

    dx_summary = _clean_text(payload.get("dx_summary"), max_length=900)
    case_context = _safe_context(payload.get("case_context"))
    raw_base_queries = payload.get("base_queries", [])
    base_queries = [query for query in raw_base_queries if isinstance(query, str)]
    anchors = _anchor_terms(dx_summary, case_context)

    system_msg = (
        "You generate retrieval search queries for an evidence-based medicine RAG system. "
        "Return one compact JSON object on a single line. Do not answer the medical question. Do not invent patient facts. "
        "Use the supplied diagnosis and treatment terms as anchors. Produce at most four concise queries."
    )
    user_payload = {
        "dx_summary": dx_summary,
        "case_context": case_context,
        "base_queries": base_queries,
        "required_json_schema": {
            "queries": [
                {"intent": "guideline", "query": "short searchable medical evidence query"},
                {"intent": "efficacy", "query": "short searchable medical evidence query"},
                {"intent": "contraindication", "query": "short searchable medical evidence query"},
                {"intent": "alternatives", "query": "short searchable medical evidence query"},
            ]
        },
    }
    messages = [
        {"role": "user", "content": f"[SYSTEM] {system_msg}\n\nINPUT JSON:\n{json.dumps(user_payload, ensure_ascii=False)}"}
    ]

    try:
        result = await adapter.chat(conn["api_key"], conn["model_id"], messages, temperature=0.0, max_tokens=900)
        raw = result.get("content", "")
        parsed, parse_error = _load_json_object(raw)
        if parsed is None:
            items, validation_errors = _loose_query_items(raw, base_queries, anchors)
            if not items:
                return {
                    "status": "failed",
                    "queries": [],
                    "items": [],
                    "model": conn["model_id"],
                    "connection_id": conn["id"],
                    "error": f"LLM returned invalid JSON: {parse_error}",
                    "validation_errors": validation_errors[:6],
                }
        else:
            items, validation_errors = _validated_queries(parsed.get("queries"), base_queries, anchors)
        return {
            "status": "ok",
            "queries": [item["query"] for item in items],
            "items": items,
            "model": conn["model_id"],
            "connection_id": conn["id"],
            "error": None if items else "no_valid_extra_queries",
            "validation_errors": validation_errors[:6],
        }
    except json.JSONDecodeError as e:
        return {
            "status": "failed",
            "queries": [],
            "items": [],
            "model": conn["model_id"],
            "connection_id": conn["id"],
            "error": f"LLM returned invalid JSON: {e}",
        }
    except Exception as e:
        return {
            "status": "failed",
            "queries": [],
            "items": [],
            "model": conn["model_id"],
            "connection_id": conn["id"],
            "error": adapter.safe_error(e, conn.get("api_key", "")),
        }


async def execute_query_decompose(payload: dict) -> dict:
    return await _execute_query_decompose_for_task("query_decompose", payload)
