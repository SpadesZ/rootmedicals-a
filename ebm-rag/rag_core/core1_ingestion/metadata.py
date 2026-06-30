# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core1_ingestion/metadata.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core1 入庫層，負責 chunk、metadata 與語料轉換契約。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core1_ingestion/metadata.py
# Timestamp: 2026-06-12
# Version: v0.2
# Description: Core1 Metadata 注入器。
#              從 chunk 文字以 regex 啟發式抽取 PMID / DOI / 年份；
#              外部 metadata 以白名單覆蓋，不確定欄位填 "unknown" 或 null，絕不使用 LLM 推測。
# ----------------------------------------------------------------------------------------------------

import re

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

    chunk["payload"] = payload
    return chunk
