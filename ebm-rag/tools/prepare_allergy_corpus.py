"""Curate the ARIA-EAACI 2024-2025 allergic rhinitis guidelines into the ebm-rag index.

Source: Europe PMC JATS full text, both parts confirmed CC BY via the Europe PMC
licence field. Run with --dry-run first: it parses, chunks and runs the same
quality gate the indexer uses, without touching sqlite or Qdrant.
"""

import argparse
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, "/app")

from rag_core.common import state_db as sdb
from rag_core.common.chunk_quality import evaluate_chunk_quality
from rag_core.core1_ingestion.chunker import (
    _CONTRAINDICATION_TERMS,
    _count_tokens,
    _split_text_to_max_tokens,
)
from rag_core.core1_ingestion.source_policy import validate_source_policy
from rag_core.core2_embeddings.pipeline import run_embedding_pipeline
from rag_core.core3_vector_store.indexer import index_paper

XML_DIR = Path("/app/data/test_fixtures")
TARGET_TOKENS = 380  # stay clear of the 500-token quality ceiling

PAPERS = {
    "RM_ARIA_INTRANASAL_2026": {
        "xml": "PMC13040648.xml",
        "pmid": "41324154",
        "doi": "10.1111/all.70131",
        "pmcid": "PMC13040648",
        "title": ("Allergic Rhinitis and Its Impact on Asthma (ARIA)-EAACI Guidelines"
                  " - 2024-2025 Revision: Part I - Guidelines on Intranasal Treatments"),
    },
    "RM_ARIA_ORAL_OCULAR_2026": {
        "xml": "PMC13256267.xml",
        "pmid": "41877472",
        "doi": "10.1111/all.70305",
        "pmcid": "PMC13256267",
        "title": ("Allergic Rhinitis and Its Impact on Asthma (ARIA)-EAACI Guidelines"
                  " - 2024-2025 Revision: Part II - Guidelines on Oral and Ocular Treatments"),
    },
}

# CC BY 4.0 confirmed programmatically via the Europe PMC `licence` field for both DOIs.
POLICY_BASE = {
    "schema": "rootmedicals-source-policy.v1",
    "document_version": "aria-eaaci-2024-2025-revision",
    "lifecycle_status": "current",
    "license_status": "approved",
    "permitted_uses": ["internal_validation", "retrieval_generation", "commercial_publication"],
    "commercial_publication_allowed": True,
    "rights_source_url": "https://creativecommons.org/licenses/by/4.0/",
    "rights_reviewed_at": "2026-09-10",
    "policy_note": ("Open access under CC BY 4.0, confirmed via the Europe PMC licence field"
                    " for this DOI. Redistribution and commercial use are permitted with"
                    " attribution to the original authors and journal (Allergy, Wiley)."),
}


DROP_TAGS = {"xref", "table-wrap", "fig", "graphic", "media", "disp-formula"}
MIN_CHUNK_TOKENS = 20


def _text_of(node) -> str:
    """Flatten an element to text, removing citation markers without orphaning brackets."""
    import copy

    node = copy.deepcopy(node)
    for parent in node.iter():
        for child in list(parent):
            if child.tag not in DROP_TAGS:
                continue
            # Keep the tail: it carries the closing bracket and following prose.
            tail = child.tail or ""
            siblings = list(parent)
            idx = siblings.index(child)
            if idx == 0:
                parent.text = (parent.text or "") + tail
            else:
                prev = siblings[idx - 1]
                prev.tail = (prev.tail or "") + tail
            parent.remove(child)

    text = "".join(node.itertext())
    # Collapse the empty brackets left behind by removed citation markers.
    text = re.sub(r"\[\s*(?:[,;–—-]\s*)*\]", "", text)
    text = re.sub(r"\(\s*(?:[,;–—-]\s*)*\)", "", text)
    text = re.sub(r"\s+([,.;:%)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+", " ", text).strip()


def _walk_sections(sec, trail):
    """Yield (title_path, paragraph_text) depth-first."""
    title_el = sec.find("title")
    title = _text_of(title_el) if title_el is not None else ""
    path = trail + [title] if title else list(trail)

    for child in sec:
        if child.tag == "p":
            text = _text_of(child)
            if text:
                yield path, text
        elif child.tag == "sec":
            yield from _walk_sections(child, path)


def extract_paragraphs(xml_path: Path):
    root = ET.parse(xml_path).getroot()
    body = root.find(".//body")
    if body is None:
        raise SystemExit("no <body> in " + str(xml_path))
    for sec in body.findall("sec"):
        yield from _walk_sections(sec, [])


def build_chunks(paper_id: str, meta: dict) -> list:
    xml_path = XML_DIR / meta["xml"]
    paragraphs = list(extract_paragraphs(xml_path))

    # Group consecutive paragraphs of the same section up to TARGET_TOKENS.
    groups, buf, buf_path, buf_tokens = [], [], None, 0
    for path, text in paragraphs:
        tokens, _ = _count_tokens(text)
        if buf and (path != buf_path or buf_tokens + tokens > TARGET_TOKENS):
            groups.append((buf_path, " ".join(buf)))
            buf, buf_tokens = [], 0
        buf_path = path
        buf.append(text)
        buf_tokens += tokens
    if buf:
        groups.append((buf_path, " ".join(buf)))

    chunks = []
    for path, text in groups:
        for part in _split_text_to_max_tokens(text):
            token_count, method = _count_tokens(part)
            if token_count < MIN_CHUNK_TOKENS:
                continue  # fragments too small to carry traceable clinical meaning
            index = len(chunks)
            section_title = path[-1] if path else ""
            payload = {
                "paper_id": paper_id,
                "filename": meta["xml"],
                "page_start": 1,
                "page_end": 1,
                "block_ids": [],
                "section_title": section_title,
                "title_path": list(path),
                "block_types": ["Text"],
                "specialty": "allergy_immunology",
                "disease": "allergic_rhinitis",
                "six_s_level": "System",
                "ocebm_level": "Level_1",
                "grade_baseline": "Grade_A",
                "source_type": "guideline",
                "publication_year": 2026,
                "pmid": meta["pmid"],
                "doi": meta["doi"],
                "journal": "Allergy",
                "guideline_title": meta["title"],
                "guideline_organization": "EAACI / ARIA",
                "guideline_year": 2026,
                "source_url": "https://pmc.ncbi.nlm.nih.gov/articles/%s/" % meta["pmcid"],
                "guideline_url": "https://doi.org/%s" % meta["doi"],
                "citation_text": "%s. Allergy. 2026. doi:%s" % (meta["title"], meta["doi"]),
                "study_design": "guideline",
                "is_guideline": True,
                "has_contraindication_terms": bool(_CONTRAINDICATION_TERMS.search(part)),
                "token_count_method": method,
            }
            chunks.append({
                "chunk_id": "%s:chunk:%06d" % (paper_id, index),
                "paper_id": paper_id,
                "chunk_index": index,
                "text": part,
                "token_count": token_count,
                "title_path": list(path),
                "payload": payload,
            })
    return chunks


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total_issues = 0
    for paper_id, meta in PAPERS.items():
        chunks = build_chunks(paper_id, meta)
        tokens = [c["token_count"] for c in chunks]
        issues = []
        for chunk in chunks:
            issues.extend(evaluate_chunk_quality(chunk))
        total_issues += len(issues)

        print("=== %s ===" % paper_id)
        print("  chunks      : %d" % len(chunks))
        print("  tokens      : min=%d max=%d avg=%d total=%d"
              % (min(tokens), max(tokens), sum(tokens) // len(tokens), sum(tokens)))
        print("  quality     : %d issues" % len(issues))
        for issue in issues[:5]:
            print("     !", issue.get("code"), issue.get("chunk_id"))
        sections = []
        for c in chunks:
            st = c["payload"]["section_title"]
            if st and st not in sections:
                sections.append(st)
        print("  sections(%d): %s" % (len(sections), "; ".join(sections[:6])))
        print("  sample      : %s" % chunks[0]["text"][:160])

        if args.dry_run:
            continue

        if issues:
            raise SystemExit("refusing to write %s: quality gate found issues" % paper_id)

        await sdb.upsert_paper(
            paper_id, meta["xml"], str(XML_DIR / meta["xml"]),
            status="core1_done", total_pages=1,
        )
        for chunk in chunks:
            await sdb.upsert_chunk(chunk)
        print("  -> upserted %d chunks" % len(chunks))

        embedded = await run_embedding_pipeline(paper_id)
        print("  -> embedding: %s" % json.dumps(embedded, ensure_ascii=False)[:220])
        if embedded.get("status") != "ok":
            raise SystemExit("embedding failed for " + paper_id)

        policy = validate_source_policy(dict(POLICY_BASE, paper_id=paper_id))
        updated = await sdb.sync_paper_source_policy(paper_id, policy)
        print("  -> source policy mirrored to %d chunks" % updated)

        indexed = await index_paper(paper_id)
        print("  -> index: %s" % json.dumps(indexed, ensure_ascii=False)[:220])
        if indexed.get("status") != "ok" or indexed.get("quality_blocked"):
            raise SystemExit("indexing failed for " + paper_id)

    if args.dry_run:
        print("\nDRY RUN - nothing written. total quality issues: %d" % total_issues)


asyncio.run(main())
