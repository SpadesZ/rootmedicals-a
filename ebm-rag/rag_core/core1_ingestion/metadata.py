# 模組定位: ebm-rag Core1 chunk metadata 注入與 evidence-scope 邊界。
# 主要責任: 合併 allowlisted source metadata、抽取 PMID/DOI/year，並產出 topic_key/slot_keys。
# 呼叫來源: rag_core/core1_ingestion/pipeline.py 的每個新 chunk。
# 輸入契約: chunk payload 與 Core5 已過濾的 external_meta；可用 slot_keys_by_chunk 精準標註。
# 輸出契約: 保留原 chunk 並加入 canonical topic/slot scope；缺 scope 時使用 conservative wildcard。
# 安全邊界: 不以 LLM 或全文關鍵字猜疾病/slot；None/unknown 不覆蓋已知 bibliographic metadata。
# 維護提醒: 新增 external metadata 欄位時需同步 Core5 allowlist 與 scope revision contract tests。
# ----------------------------------------------------------------------------------------------------

import re

from rag_core.common.evidence_scope import normalize_evidence_scope

def inject_metadata(chunk: dict, external_meta: dict = None) -> dict:
    """Inject known metadata from external source or PDF text heuristics."""
    if not external_meta:
        external_meta = {}
    text = chunk.get("text", "")
    payload = chunk["payload"]

    # PMID / DOI heuristics from text
    pmid_match = re.search(r"\bPMID[:\s]+(\d{6,9})\b", text)
    doi_match = re.search(r"\b(10\.\d{4,}/\S+)", text)
    year_match = re.search(r"\b(19|20)\d{2}\b", text)

    if pmid_match and not payload.get("pmid"):
        payload["pmid"] = pmid_match.group(1)
    if doi_match and not payload.get("doi"):
        payload["doi"] = doi_match.group(1)
    if year_match and not payload.get("publication_year"):
        payload["publication_year"] = int(year_match.group(0))

    # Merge external metadata (never overwrite with None/unknown)
    for key in ("specialty", "disease", "six_s_level", "ocebm_level",
                "grade_baseline", "source_type", "journal", "study_design",
                "is_guideline", "pmid", "doi", "publication_year",
                "guideline_title", "guideline_organization", "guideline_year",
                "source_url", "guideline_url", "citation_text"):
        ext_val = external_meta.get(key)
        if ext_val not in (None, "unknown", ""):
            payload[key] = ext_val

    scope_payload = dict(payload)
    if "topic_key" in external_meta:
        scope_payload["topic_key"] = external_meta["topic_key"]
    slot_keys_by_chunk = external_meta.get("slot_keys_by_chunk")
    if isinstance(slot_keys_by_chunk, dict):
        chunk_scope = slot_keys_by_chunk.get(
            str(chunk.get("chunk_id") or ""),
            slot_keys_by_chunk.get(str(chunk.get("chunk_index", ""))),
        )
        if chunk_scope is not None:
            scope_payload["slot_keys"] = chunk_scope
    if "slot_keys" in external_meta and "slot_keys" not in scope_payload:
        scope_payload["slot_keys"] = external_meta["slot_keys"]
    topic_key, slot_keys = normalize_evidence_scope(scope_payload)
    payload["topic_key"] = topic_key
    payload["slot_keys"] = slot_keys

    chunk["payload"] = payload
    return chunk
