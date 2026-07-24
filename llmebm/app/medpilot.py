# 模組定位: llmebm Medpilot 單輪問答的 RAG trust-boundary formatter。
# 主要責任: 處理 deterministic 招呼語，並把既有 ebm-rag ebm_hits 轉成可安全顯示的 answer/section/citation contract。
# 呼叫來源: main_ebm.py 的 POST /api/v1/medpilot/query 與無網路 contract tests。
# 輸入契約: ebm-rag /api/v1/rag/query 回傳的 dict；臨床段落的 chunk_id 必須存在於本次 retrieval chunks。
# 輸出契約: rootmedicals-medpilot-answer.v1；證據不完整時只回 insufficient_evidence，不回部分臨床答案。
# 安全邊界: 不呼叫模型、不寫資料庫、不接受模型自報但 retrieval 無法核對的 citation，也不外洩 provider 錯誤細節。
# 維護提醒: 這是單輪唯讀 MVP；對話記憶、病人 context 與疾病頁寫入不得偷偷加在此層。
# ----------------------------------------------------------------------------------------------------

import re


SCHEMA = "rootmedicals-medpilot-answer.v1"
_SMALLTALK = {
    "hi", "hello", "hey", "goodmorning", "goodafternoon", "goodevening",
    "哈囉", "哈啰", "你好", "您好", "嗨", "在嗎", "早安", "午安", "晚安",
}
_NO_DIRECT_EVIDENCE = (
    "lacking direct evidence",
    "lack direct evidence",
    "insufficient direct evidence",
    "no direct evidence",
)


def _normal_text(value, limit=4000):
    return str(value or "").strip()[:limit]


def _insufficient(query_id=""):
    return {
        "schema": SCHEMA,
        "status": "insufficient_evidence",
        "kind": "insufficient_evidence",
        "answer": "本地 EBM 索引目前沒有足夠且可追溯的直接證據，因此系統不使用模型既有知識補答。請改寫問題或等待新增合適文獻。",
        "sections": [],
        "citations": [],
        "warnings": [],
        "query_id": _normal_text(query_id, 120),
        "rag_called": True,
    }


def smalltalk_response(query):
    """Return a deterministic greeting only for exact short greetings; all other text goes to RAG."""
    normalized = re.sub(r"[\s\W_]+", "", str(query or "").strip().lower(), flags=re.UNICODE)
    if normalized not in _SMALLTALK:
        return None
    return {
        "schema": SCHEMA,
        "status": "ok",
        "kind": "smalltalk",
        "answer": "哈囉！我是 Medpilot。你可以詢問本地 EBM 索引涵蓋的臨床問題；醫療回答只會使用可追溯的本地證據。",
        "sections": [],
        "citations": [],
        "warnings": [],
        "query_id": "",
        "rag_called": False,
    }


def _contains_no_direct_evidence(text):
    normalized = _normal_text(text).lower()
    return any(marker in normalized for marker in _NO_DIRECT_EVIDENCE)


def _citation_from_source(source, chunk):
    if not isinstance(source, dict) or not isinstance(chunk, dict):
        return None
    chunk_id = _normal_text(source.get("chunk_id"), 300)
    if not chunk_id or chunk_id != _normal_text(chunk.get("chunk_id"), 300):
        return None

    payload = chunk.get("payload") if isinstance(chunk.get("payload"), dict) else {}
    paper_id = _normal_text(source.get("paper_id") or chunk.get("paper_id") or payload.get("paper_id"), 200)
    if not paper_id:
        return None

    citation = {"paper_id": paper_id, "chunk_id": chunk_id}
    for key in ("pmid", "doi"):
        supplied = _normal_text(source.get(key), 240)
        retrieved = _normal_text(chunk.get(key) or payload.get(key), 240)
        if supplied and supplied != retrieved:
            return None
        citation[key] = supplied or retrieved or None

    citation["title"] = _normal_text(
        payload.get("guideline_title") or payload.get("citation_text") or payload.get("filename") or paper_id,
        500,
    )
    score = source.get("score")
    citation["score"] = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    return citation


def build_medpilot_response(rag_result):
    """Convert validated RAG output; any untraceable clinical section makes the whole answer fail closed."""
    if not isinstance(rag_result, dict):
        return _insufficient()
    query_id = rag_result.get("query_id")
    comments = rag_result.get("rag_comments")
    chunks = ((rag_result.get("retrieval") or {}).get("chunks")) if isinstance(rag_result.get("retrieval"), dict) else None
    if rag_result.get("status") != "ok" or not isinstance(comments, list) or not comments or not isinstance(chunks, list):
        return _insufficient(query_id)

    chunk_map = {
        _normal_text(chunk.get("chunk_id"), 300): chunk
        for chunk in chunks
        if isinstance(chunk, dict) and _normal_text(chunk.get("chunk_id"), 300)
    }
    summary = _normal_text(rag_result.get("short_comment"), 3000)
    if not chunk_map or _contains_no_direct_evidence(summary):
        return _insufficient(query_id)

    citations = []
    citation_indexes = {}
    sections = []
    for comment in comments:
        if not isinstance(comment, dict):
            return _insufficient(query_id)
        text = _normal_text(comment.get("comment"), 8000)
        sources = comment.get("sources")
        if not text or _contains_no_direct_evidence(text) or not isinstance(sources, list) or not sources:
            return _insufficient(query_id)

        indexes = []
        for source in sources:
            chunk_id = _normal_text(source.get("chunk_id"), 300) if isinstance(source, dict) else ""
            citation = _citation_from_source(source, chunk_map.get(chunk_id))
            if citation is None:
                return _insufficient(query_id)
            if chunk_id not in citation_indexes:
                citation_indexes[chunk_id] = len(citations) + 1
                citations.append(citation)
            indexes.append(citation_indexes[chunk_id])

        sections.append({
            "topic": _normal_text(comment.get("topic") or "Evidence summary", 300),
            "text": text,
            "evidence_level": _normal_text(comment.get("evidence_level") or "unknown", 40),
            "grade": _normal_text(comment.get("grade") or "unknown", 40),
            "citation_indexes": list(dict.fromkeys(indexes)),
        })

    if not summary:
        summary = sections[0]["text"]
    warnings = []
    for warning in rag_result.get("warnings", [])[:5] if isinstance(rag_result.get("warnings"), list) else []:
        if isinstance(warning, dict):
            warnings.append({
                "code": _normal_text(warning.get("code"), 100),
                "message": _normal_text(warning.get("message"), 500),
            })

    return {
        "schema": SCHEMA,
        "status": "ok",
        "kind": "evidence_answer",
        "answer": summary,
        "sections": sections,
        "citations": citations,
        "warnings": warnings,
        "query_id": _normal_text(query_id, 120),
        "light_color": _normal_text(rag_result.get("light_color") or "yellow", 20),
        "rag_called": True,
    }
