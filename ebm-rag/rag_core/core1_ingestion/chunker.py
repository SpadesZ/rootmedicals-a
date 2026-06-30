# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core1_ingestion/chunker.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core1 入庫層，負責 chunk、metadata 與語料轉換契約。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core1_ingestion/chunker.py
# Timestamp: 2026-06-12
# Version: v0.5
# Description: Core1 語意切塊引擎。
#              依 page_num/obj_index 排序 → 辨識 Title 層級 → 累積 300-500 token chunks → 30-50 token overlap。
#              優先 tiktoken；不可用時降級為 word_estimate 並在 payload 標明方法。
# ----------------------------------------------------------------------------------------------------

import re
_SECTION_PATTERNS = [
    r"^\s*((\d+(\.\d+)*|[IVX]+)\.?\s+)?(Abstract|Introduction|Background|Methods?|Materials?\s+and\s+Methods?|Results?|"
    r"Discussion|Conclusion|Conclusions?|References?|Acknowledgements?|"
    r"Diagnosis|Treatment|Therapy|Management|Epidemiology|Pathophysiology|"
    r"Prognosis|Prevention|Screening|Etiology|Aetiology|Recommendations?|"
    r"Summary|Objectives?|Aims?|Purpose|Outcomes?|Limitations?|Patients?|Participants?|"
    r"Interventions?|Eligibility|Adverse\s+Events?|Safety|Contraindications?)\b.*$"
]
_TITLE_RE = re.compile("|".join(_SECTION_PATTERNS), re.IGNORECASE)

_CONTRAINDICATION_TERMS = re.compile(
    r"\b(contraindicat\w*|fatal|lethal|death|overdose|hypersensitiv|anaphylaxis|"
    r"severe adverse|black.?box|do not use|avoid in|prohibited)\b", re.IGNORECASE
)

MAX_TOKENS = 500
OVERLAP_TOKENS = 40


def _count_tokens(text: str) -> tuple[int, str]:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), "tiktoken"
    except Exception:
        return len(text.split()), "word_estimate"


def _split_text_to_max_tokens(text: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return []
    token_count, method = _count_tokens(clean_text)
    if token_count <= max_tokens:
        return [clean_text]

    if method == "tiktoken":
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            tokens = enc.encode(clean_text)
            parts = []
            safe_limit = max(1, max_tokens - 8)
            for start in range(0, len(tokens), safe_limit):
                decoded = enc.decode(tokens[start:start + safe_limit]).strip()
                if decoded:
                    decoded_count, _ = _count_tokens(decoded)
                    if decoded_count <= max_tokens:
                        parts.append(decoded)
                    else:
                        words = decoded.split()
                        word_buffer: list[str] = []
                        for word in words:
                            word_candidate = " ".join(word_buffer + [word])
                            word_tokens, _ = _count_tokens(word_candidate)
                            if word_tokens > max_tokens and word_buffer:
                                parts.append(" ".join(word_buffer))
                                word_buffer = [word]
                            else:
                                word_buffer.append(word)
                        if word_buffer:
                            parts.append(" ".join(word_buffer))
            return parts
        except Exception:
            pass

    sentences = re.split(r"(?<=[.!?;。！？；])\s+", clean_text)
    parts: list[str] = []
    buffer = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = sentence if not buffer else f"{buffer} {sentence}"
        candidate_tokens, _ = _count_tokens(candidate)
        if candidate_tokens <= max_tokens:
            buffer = candidate
            continue
        if buffer:
            parts.append(buffer)
            buffer = ""
        sentence_tokens, _ = _count_tokens(sentence)
        if sentence_tokens <= max_tokens:
            buffer = sentence
            continue
        words = sentence.split()
        if not words:
            for start in range(0, len(sentence), max_tokens * 4):
                piece = sentence[start:start + max_tokens * 4].strip()
                if piece:
                    parts.append(piece)
            continue
        word_buffer: list[str] = []
        for word in words:
            word_candidate = " ".join(word_buffer + [word])
            word_tokens, _ = _count_tokens(word_candidate)
            if word_tokens > max_tokens and word_buffer:
                parts.append(" ".join(word_buffer))
                word_buffer = [word]
            else:
                word_buffer.append(word)
        if word_buffer:
            parts.append(" ".join(word_buffer))
    if buffer:
        parts.append(buffer)
    return [part for part in parts if part.strip()]


def _make_chunk_id(paper_id: str, index: int) -> str:
    return f"{paper_id}:chunk:{index:06d}"


def _infer_title_level(text: str) -> int:
    stripped = text.strip()
    if _TITLE_RE.match(stripped):
        if re.match(r"^\s*\d+\.\d+", stripped):
            return 2
        return 1
    return 2


def chunk_document(paper_id: str, filename: str, document_stream: list) -> list:
    blocks = sorted(document_stream, key=lambda b: (b.get("page_num", 0), b.get("obj_index", 0)))

    chunks = []
    chunk_index = 0
    current_title_path: list[str] = []
    buffer_blocks: list[dict] = []
    buffer_text = ""
    buffer_tokens = 0
    token_method = "word_estimate"

    def build_payload(text: str, blocks_for_chunk: list[dict], block_type_override: list[str] = None) -> dict:
        page_nums = [b.get("page_num", 1) for b in blocks_for_chunk]
        block_ids = [b.get("block_id", "") for b in blocks_for_chunk]
        block_types = block_type_override or list({b.get("type", "Text") for b in blocks_for_chunk})
        _, meth = _count_tokens(text)
        has_contra = bool(_CONTRAINDICATION_TERMS.search(text))
        return {
            "paper_id": paper_id,
            "filename": filename,
            "page_start": min(page_nums) if page_nums else 1,
            "page_end": max(page_nums) if page_nums else 1,
            "block_ids": block_ids,
            "section_title": current_title_path[-1] if current_title_path else "",
            "title_path": list(current_title_path),
            "block_types": block_types,
            "specialty": "unknown",
            "disease": "unknown",
            "six_s_level": "unknown",
            "ocebm_level": "unknown",
            "grade_baseline": "unknown",
            "source_type": "unknown",
            "publication_year": None,
            "pmid": None,
            "doi": None,
            "journal": None,
            "guideline_title": None,
            "guideline_organization": None,
            "guideline_year": None,
            "source_url": None,
            "guideline_url": None,
            "citation_text": None,
            "study_design": "unknown",
            "is_guideline": False,
            "has_contraindication_terms": has_contra,
            "token_count_method": meth
        }

    def append_chunk_text(text: str, blocks_for_chunk: list[dict], block_type_override: list[str] = None, extra_payload: dict = None):
        nonlocal chunk_index
        for part in _split_text_to_max_tokens(text):
            tok, _ = _count_tokens(part)
            cid = _make_chunk_id(paper_id, chunk_index)
            payload = build_payload(part, blocks_for_chunk, block_type_override)
            if extra_payload:
                payload.update(extra_payload)
            chunks.append({
                "chunk_id": cid,
                "paper_id": paper_id,
                "chunk_index": chunk_index,
                "text": part,
                "token_count": tok,
                "title_path": list(current_title_path),
                "payload": payload,
                "embedding_status": "pending",
                "vector_id": None
            })
            chunk_index += 1

    def flush_buffer():
        nonlocal chunk_index, buffer_text, buffer_blocks, buffer_tokens
        if not buffer_text.strip():
            return
        append_chunk_text(buffer_text, buffer_blocks)
        # overlap: keep last OVERLAP_TOKENS words
        words = buffer_text.split()
        if len(words) > OVERLAP_TOKENS:
            overlap_text = " ".join(words[-OVERLAP_TOKENS:])
        else:
            overlap_text = buffer_text
        buffer_text = overlap_text
        buffer_tokens, _ = _count_tokens(overlap_text)
        buffer_blocks = []

    for blk in blocks:
        btype = blk.get("type", "Text")
        text = (blk.get("text") or "").strip()
        if not text:
            continue

        if btype == "Title":
            # flush current buffer first
            flush_buffer()
            buffer_blocks = []
            buffer_text = ""
            buffer_tokens = 0
            level = _infer_title_level(text)
            if level == 1:
                current_title_path = [text]
            else:
                if len(current_title_path) > 1:
                    current_title_path = current_title_path[:1] + [text]
                else:
                    current_title_path = current_title_path + [text] if current_title_path else [text]
            # don't add short titles alone; they'll prepend next text
            continue

        if btype in ("Table", "Figure", "Caption"):
            # flush existing buffer
            flush_buffer()
            append_chunk_text(text, [blk], block_type_override=[btype], extra_payload={"image_url": blk.get("image_url")})
            buffer_blocks = []
            buffer_text = ""
            buffer_tokens = 0
            continue

        # Text block
        prefix = "\n".join(current_title_path) + "\n" if current_title_path else ""
        candidate = (prefix + text) if not buffer_text else (buffer_text + "\n" + text)
        tok, meth = _count_tokens(candidate)
        token_method = meth

        if tok > MAX_TOKENS and buffer_text:
            flush_buffer()
            candidate = (prefix + text) if current_title_path else text
            buffer_text = candidate
            buffer_blocks = [blk]
            buffer_tokens, _ = _count_tokens(buffer_text)
        else:
            buffer_text = candidate
            buffer_blocks.append(blk)
            buffer_tokens = tok

    flush_buffer()
    return chunks
