# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/common/chunk_quality.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 共用工具層，集中錯誤、設定、品質與狀態資料庫工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/common/chunk_quality.py
# Timestamp: 2026-06-12
# Version: v0.2
# Description: Shared chunk quality gate for indexed and retrieved RAG evidence.
#              Fail-closed rules keep unsafe or non-traceable chunks out of clinical query flow.
# ----------------------------------------------------------------------------------------------------

from typing import Any

MAX_SAFE_CHUNK_TOKENS = 500

_UNKNOWN_VALUES = {"", "unknown", "null", "none", "n/a", "na", "-"}
_REQUIRED_EBM_FIELDS = ("six_s_level", "ocebm_level", "source_type")
_REQUIRED_CLINICAL_FIELDS = ("specialty", "disease")
_REQUIRED_BOOL_FIELDS = ("is_guideline", "has_contraindication_terms")
_GUIDELINE_CITATION_FIELDS = ("guideline_title", "guideline_organization")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_unknown(value: Any) -> bool:
    text = _clean_text(value).lower()
    return text in _UNKNOWN_VALUES


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _payload_for(chunk: dict) -> dict:
    payload = chunk.get("payload")
    if isinstance(payload, dict):
        return payload
    return {}


def _chunk_value(chunk: dict, payload: dict, key: str) -> Any:
    value = chunk.get(key)
    if value is None or _is_unknown(value):
        return payload.get(key)
    return value


def _is_guideline_source(chunk: dict, payload: dict) -> bool:
    source_type = _clean_text(_chunk_value(chunk, payload, "source_type")).lower()
    return _chunk_value(chunk, payload, "is_guideline") is True or source_type == "guideline"


def _has_guideline_citation_card(chunk: dict, payload: dict) -> bool:
    if not _is_guideline_source(chunk, payload):
        return False

    for field_name in _GUIDELINE_CITATION_FIELDS:
        if _is_unknown(_chunk_value(chunk, payload, field_name)):
            return False

    guideline_year = _safe_int(_chunk_value(chunk, payload, "guideline_year"))
    publication_year = _safe_int(_chunk_value(chunk, payload, "publication_year"))
    if guideline_year is None and publication_year is None:
        return False

    source_url = _chunk_value(chunk, payload, "source_url")
    guideline_url = _chunk_value(chunk, payload, "guideline_url")
    if _is_unknown(source_url) and _is_unknown(guideline_url):
        return False

    return True


def evaluate_chunk_quality(chunk: dict, max_tokens: int = MAX_SAFE_CHUNK_TOKENS) -> list[dict]:
    safe_chunk = chunk if isinstance(chunk, dict) else {}
    payload = _payload_for(safe_chunk)
    issues: list[dict] = []

    chunk_id = _clean_text(_chunk_value(safe_chunk, payload, "chunk_id")) or "unknown_chunk"
    paper_id = _clean_text(_chunk_value(safe_chunk, payload, "paper_id")) or "unknown_paper"
    token_count = _safe_int(_chunk_value(safe_chunk, payload, "token_count"))
    text = _clean_text(_chunk_value(safe_chunk, payload, "text"))

    if not text:
        issues.append({
            "code": "chunk_text_empty",
            "message": "Chunk text is empty",
            "chunk_id": chunk_id,
            "paper_id": paper_id
        })

    if token_count is None or token_count <= 0:
        issues.append({
            "code": "chunk_token_count_invalid",
            "message": "Chunk token_count is missing or invalid",
            "chunk_id": chunk_id,
            "paper_id": paper_id,
            "token_count": token_count
        })
    elif token_count > int(max_tokens):
        issues.append({
            "code": "chunk_token_limit_exceeded",
            "message": f"Chunk token_count exceeds {int(max_tokens)}",
            "chunk_id": chunk_id,
            "paper_id": paper_id,
            "token_count": token_count,
            "max_tokens": int(max_tokens)
        })

    for field_name in _REQUIRED_EBM_FIELDS:
        if _is_unknown(_chunk_value(safe_chunk, payload, field_name)):
            issues.append({
                "code": f"missing_{field_name}",
                "message": f"Required EBM metadata is missing: {field_name}",
                "chunk_id": chunk_id,
                "paper_id": paper_id,
                "field": field_name
            })

    for field_name in _REQUIRED_CLINICAL_FIELDS:
        if _is_unknown(_chunk_value(safe_chunk, payload, field_name)):
            issues.append({
                "code": f"missing_{field_name}",
                "message": f"Required clinical metadata is missing: {field_name}",
                "chunk_id": chunk_id,
                "paper_id": paper_id,
                "field": field_name
            })

    pmid = _chunk_value(safe_chunk, payload, "pmid")
    doi = _chunk_value(safe_chunk, payload, "doi")
    if _is_unknown(pmid) and _is_unknown(doi) and not _has_guideline_citation_card(safe_chunk, payload):
        issues.append({
            "code": "missing_citation",
            "message": "Chunk has neither PMID/DOI nor a complete guideline citation card",
            "chunk_id": chunk_id,
            "paper_id": paper_id
        })

    for field_name in _REQUIRED_BOOL_FIELDS:
        value = _chunk_value(safe_chunk, payload, field_name)
        if not isinstance(value, bool):
            issues.append({
                "code": f"invalid_{field_name}",
                "message": f"Boolean metadata is invalid: {field_name}",
                "chunk_id": chunk_id,
                "paper_id": paper_id,
                "field": field_name
            })

    return issues


def summarize_chunk_quality(chunks: list, max_tokens: int = MAX_SAFE_CHUNK_TOKENS) -> dict:
    safe_chunks = chunks if isinstance(chunks, list) else []
    counts_by_code: dict[str, int] = {}
    sample_issues: list[dict] = []
    paper_map: dict[str, dict] = {}
    passed_chunk_count = 0
    issue_chunk_count = 0

    for chunk in safe_chunks:
        safe_chunk = chunk if isinstance(chunk, dict) else {}
        payload = _payload_for(safe_chunk)
        paper_id = _clean_text(_chunk_value(safe_chunk, payload, "paper_id")) or "unknown_paper"
        paper_entry = paper_map.setdefault(paper_id, {
            "paper_id": paper_id,
            "chunk_count": 0,
            "issue_chunk_count": 0
        })
        paper_entry["chunk_count"] += 1

        issues = evaluate_chunk_quality(safe_chunk, max_tokens=max_tokens)
        if issues:
            issue_chunk_count += 1
            paper_entry["issue_chunk_count"] += 1
            for issue in issues:
                issue_code = issue.get("code") or "unknown_quality_issue"
                counts_by_code[issue_code] = counts_by_code.get(issue_code, 0) + 1
                if len(sample_issues) < 12:
                    sample_issues.append(issue)
        else:
            passed_chunk_count += 1

    total_count = len(safe_chunks)
    query_safe = total_count > 0 and issue_chunk_count == 0
    return {
        "query_safe": query_safe,
        "max_tokens": int(max_tokens),
        "indexed_chunk_count": total_count,
        "passed_chunk_count": passed_chunk_count,
        "issue_chunk_count": issue_chunk_count,
        "counts_by_code": counts_by_code,
        "sample_issues": sample_issues,
        "paper_count": len(paper_map),
        "papers": sorted(paper_map.values(), key=lambda item: item["paper_id"])
    }
