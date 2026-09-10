# 模組定位: llmebm Medpilot 單輪問答的 RAG trust-boundary formatter。
# 主要責任: 處理 deterministic 招呼語，並把既有 ebm-rag ebm_hits 轉成可安全顯示的 answer/section/citation contract。
# 呼叫來源: main_ebm.py 的 POST /api/v1/medpilot/query 與無網路 contract tests。
# 輸入契約: ebm-rag /api/v1/rag/query 回傳的 dict；臨床段落的 chunk_id 必須存在於本次 retrieval chunks。
# 輸出契約: rootmedicals-medpilot-answer.v1；證據不完整時只回 insufficient_evidence，不回部分臨床答案。
#           不含 light_color：燈號需要病歷/ICD/處置 context，自由問答沒有，回了只會誤導。
# 安全邊界: 不呼叫模型、不寫資料庫、不接受模型自報但 retrieval 無法核對的 citation，也不外洩 provider 錯誤細節。
# 維護提醒: 這是單輪唯讀 MVP；對話記憶、病人 context 與疾病頁寫入不得偷偷加在此層。
# ----------------------------------------------------------------------------------------------------

import re


SCHEMA = "rootmedicals-medpilot-answer.v1"
_SMALLTALK = {
    "hi", "hello", "hey", "goodmorning", "goodafternoon", "goodevening",
    "哈囉", "哈啰", "你好", "您好", "嗨", "在嗎", "早安", "午安", "晚安",
}
# 這道守衛擋的是「模型自己說沒有直接證據，系統卻照樣顯示成臨床答案」。
# 舊版是四個字面片語的子字串比對，配合 prompts.py 規則 5 要求模型寫出
# "lacking direct evidence"。但模型會改寫語序：實測 AF 抗凝那題回的是
# "Direct evidence is lacking regarding starting anticoagulation..."，
# 四個 pattern 一個都沒中，於是 Medpilot 回了 status=ok 的臨床答案，
# 而答案第一句自己說沒有直接證據 —— 正是這道守衛要擋的情況。
# 改成涵蓋兩種語序與中文說法（回答語言會跟著提問語言走）。
# 判斷刻意偏嚴：誤判只是少答一題，漏判則會顯示不該顯示的臨床內容。
_ABSENCE = r"lack(?:s|ing|ed)?|insufficient|absence|absent|unavailable|missing"
_LINK = r"is|are|was|were|remains?|appears?|seems?"
# 中間允許的字刻意用 [A-Za-z]+ 而非 \w+：遇到標點就斷開，
# 才不會把「no benefit; direct evidence supports ...」誤判成缺證據。
_NO_DIRECT_EVIDENCE_RE = re.compile(
    # lacking / insufficient / lack of ... direct evidence
    rf"(?:{_ABSENCE})(?:\s+[A-Za-z]+){{0,2}}\s+direct\s+evidence"
    # no direct evidence / no strong direct evidence
    rf"|\bno(?:\s+[A-Za-z]+){{0,1}}\s+direct\s+evidence"
    # direct evidence is lacking / direct evidence for X was insufficient
    rf"|direct\s+evidence(?:\s+[A-Za-z]+){{0,3}}\s+(?:{_LINK})\s+(?:[A-Za-z]+\s+){{0,1}}(?:{_ABSENCE})"
    # 缺乏／沒有／無……直接證據
    r"|(?:缺乏|缺少|沒有|没有|無|无)[^。；;\n]{0,8}直接(?:證據|证据)"
    # 直接證據……不足／闕如／不存在
    r"|直接(?:證據|证据)[^。；;\n]{0,8}(?:不足|缺乏|缺少|闕如|阙如|不存在)",
    re.IGNORECASE,
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
    # 不再 .lower()：regex 已帶 IGNORECASE，中文也不受大小寫影響。
    return bool(_NO_DIRECT_EVIDENCE_RE.search(_normal_text(text)))


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
        # 這裡刻意不回 light_color。燈號是「病歷 + ICD + 處置」三者齊全時的判讀結果，
        # Medpilot 是沒有病歷 context 的自由問答，ebm-rag 一律回 yellow 並附
        # icd_missing —— 那不是證據品質的判斷，只是「這裡本來就沒有 ICD」。
        # 瀏覽器端 appendEvidenceAnswer 從來沒有渲染它，留著只會誤導 API 使用者。
        "rag_called": True,
    }
