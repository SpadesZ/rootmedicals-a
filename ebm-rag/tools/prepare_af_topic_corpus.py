# 模組定位: 既有 AF fixture 的精準 Topic scope 回填與可信 guideline slice 入庫工具。
# 主要責任: 保留 legacy vectors、更新 27 個明確 chunk scopes，並擷取人工指定 PDF 頁面進既有 Core1-3。
# 呼叫來源: P1 corpus remediation、交付前 verify-only gate 與開發者手動重建。
# 輸入契約: repo 內公開 2023 ACC/AHA AF guideline PDF、既有 fixture chunk IDs 與可用 embedding binding。
# 輸出契約: JSON summary；任何 chunk/page/index 不合約即非零退出，不留下假成功判定。
# 安全邊界: 不用 LLM/關鍵字猜 slot、不抓外網、不改臨床 validator、不重算 legacy embeddings。
# 維護提醒: 本表只適用固定 fixture；來源或 headings 改版時需人工重審頁面與 mapping 後再 force。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

import fitz

EBM_RAG_ROOT = Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from rag_core.common import state_db as sdb
from rag_core.common.evidence_scope import normalize_evidence_scope
from rag_core.core1_ingestion.affected_slot_map import enforce_demo_scope_review, load_affected_slot_map
from rag_core.core1_ingestion.pipeline import run_ingestion
from rag_core.core1_ingestion.source_policy import load_source_policy_registry, validate_source_policy
from rag_core.core2_embeddings.pipeline import run_embedding_pipeline
from rag_core.core3_vector_store.indexer import index_paper


SOURCE_PDF = EBM_RAG_ROOT / "data" / "test_fixtures" / "2023_acc_aha_af_guideline.pdf"
TOPIC_KEY = "atrial-fibrillation"
SOURCE_URL = (
    "https://www.heart.org/-/media/Files/Professional/Quality-Improvement/"
    "Get-With-the-Guidelines/Get-With-The-Guidelines-AFIB/AFib-Month/"
    "joglaretal20232023accahaaccphrsguidelineforthediagnosisandmanagementofatrialfibrillation.pdf"
)

U1 = ["universal:u1"]
U1_EVAL = ["universal:u1-1"]
U1_MANAGEMENT = ["universal:u1-2"]
BACKGROUND = ["universal:u2", "universal:u2-1"]
EPIDEMIOLOGY = ["universal:u2", "universal:u2-2", "universal:u2-2-1"]
DIAGNOSIS = [
    "universal:u1-1", "universal:u3", "universal:u3-1", "universal:u3-2",
    "universal:u3-2-1", "universal:u3-2-2",
]
MANAGEMENT = [
    "universal:u1-2", "universal:u4", "universal:u4-1", "universal:u4-2",
    "universal:u4-2-1", "universal:u4-2-2",
]
GUIDELINES = ["universal:u5", "universal:u5-2-1"]

# ponytail: This is a deliberately fixture-specific, human-audited map. The ceiling is a new
# source/chunk layout; the upgrade path is source-native section metadata, not a text heuristic.
LEGACY_SCOPES = {
    "RM_AF_GUIDE_2023": {
        0: GUIDELINES,
        1: ["universal:u5-2-1"],
        2: ["universal:u5-2-1"],
        3: ["universal:u5-2-1"],
        4: U1 + BACKGROUND + GUIDELINES,
        5: GUIDELINES,
        6: BACKGROUND,
        7: EPIDEMIOLOGY + ["universal:u4", "universal:u4-1"],
        8: MANAGEMENT,
        9: MANAGEMENT,
        10: MANAGEMENT,
        11: ["universal:u4", "universal:u4-1", "universal:u4-2-2"],
        12: MANAGEMENT,
        13: GUIDELINES,
        14: U1 + MANAGEMENT,
        15: U1 + MANAGEMENT,
        16: U1 + ["universal:u4-1"] + GUIDELINES,
        17: GUIDELINES,
        18: GUIDELINES,
        19: GUIDELINES,
        20: GUIDELINES,
        21: U1 + BACKGROUND + GUIDELINES,
        22: ["universal:u2-1", "universal:u5-2-1"],
        23: U1 + GUIDELINES,
    },
    "RM_AF_CONTRA_2023": {0: MANAGEMENT, 1: MANAGEMENT, 2: MANAGEMENT},
}

SLICE_SPECS = (
    {
        "paper_id": "RM_AF_DEFINITIONS_PATHOPHYS_2023",
        "pages": tuple(range(15, 24)),
        "slot_keys": BACKGROUND,
    },
    {
        "paper_id": "RM_AF_EPIDEMIOLOGY_2023",
        "pages": tuple(range(10, 15)),
        "slot_keys": EPIDEMIOLOGY,
    },
    {
        "paper_id": "RM_AF_EVALUATION_2023",
        "pages": tuple(range(24, 28)),
        "slot_keys": DIAGNOSIS,
    },
    {
        "paper_id": "RM_AF_RISK_MODIFICATION_2023",
        "pages": tuple(range(28, 33)),
        "slot_keys": MANAGEMENT + EPIDEMIOLOGY,
    },
    {
        "paper_id": "RM_AF_ANTICOAG_2023",
        "pages": tuple(range(33, 49)),
        "slot_keys": MANAGEMENT,
    },
    {
        "paper_id": "RM_AF_RATE_CONTROL_2023",
        "pages": tuple(range(66, 73)),
        "slot_keys": MANAGEMENT,
    },
    {
        "paper_id": "RM_AF_RHYTHM_CONTROL_2023",
        "pages": (74, 75, 76, 77, 90, 91, 92, 93),
        "slot_keys": U1_MANAGEMENT + [
            "universal:u4", "universal:u4-1", "universal:u4-2", "universal:u4-2-2",
        ],
    },
)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _expected_slots(
    base_slots: list[str], inverse: dict[tuple[str, int], list[str]], paper_id: str, chunk_index: int,
) -> list[str]:
    return enforce_demo_scope_review(
        paper_id, _dedupe(base_slots + inverse.get((paper_id, chunk_index), [])),
    )


def _validate_definitions(inverse: dict[tuple[str, int], list[str]]) -> None:
    expected = {"RM_AF_GUIDE_2023": set(range(24)), "RM_AF_CONTRA_2023": set(range(3))}
    for paper_id, mapping in LEGACY_SCOPES.items():
        if set(mapping) != expected[paper_id]:
            raise ValueError(f"{paper_id} mapping must cover exactly {sorted(expected[paper_id])}")
        for slot_keys in mapping.values():
            topic, normalized = normalize_evidence_scope({"topic_key": TOPIC_KEY, "slot_keys": _dedupe(slot_keys)})
            if topic != TOPIC_KEY or normalized == ["*"]:
                raise ValueError(f"{paper_id} contains an invalid precise slot mapping")
    paper_ids = [spec["paper_id"] for spec in SLICE_SPECS]
    if len(paper_ids) != len(set(paper_ids)):
        raise ValueError("slice paper IDs must be unique")
    known_papers = set(LEGACY_SCOPES) | set(paper_ids)
    for (paper_id, _chunk_index), slot_keys in inverse.items():
        topic, normalized = normalize_evidence_scope({"topic_key": TOPIC_KEY, "slot_keys": slot_keys})
        if paper_id not in known_papers or topic != TOPIC_KEY or normalized == ["*"]:
            raise ValueError(f"affected-slot mapping contains an invalid source selector: {paper_id}")


def _external_meta(slot_keys: list[str]) -> dict:
    return {
        "specialty": "cardiology",
        "disease": "atrial_fibrillation",
        "topic_key": TOPIC_KEY,
        "slot_keys": _dedupe(slot_keys),
        "six_s_level": "Summaries",
        "ocebm_level": "Level_1",
        "grade_baseline": "A",
        "source_type": "guideline",
        "publication_year": 2024,
        "doi": "10.1016/j.jacc.2023.08.017",
        "journal": "Journal of the American College of Cardiology",
        "study_design": "clinical_practice_guideline",
        "is_guideline": True,
        "guideline_title": "2023 ACC/AHA/ACCP/HRS Guideline for the Diagnosis and Management of Atrial Fibrillation",
        "guideline_organization": "ACC/AHA/ACCP/HRS",
        "guideline_year": 2023,
        "source_url": SOURCE_URL,
        "guideline_url": SOURCE_URL,
        "citation_text": "Joglar JA, et al. 2023 ACC/AHA/ACCP/HRS Guideline for Atrial Fibrillation. JACC. 2024;83:109-279.",
    }


async def _backfill_legacy(verify_only: bool, inverse: dict[tuple[str, int], list[str]]) -> dict:
    summary = {"updated": 0, "unchanged": 0, "papers": {}}
    for paper_id, mapping in LEGACY_SCOPES.items():
        rows = await sdb.get_chunks_for_paper(paper_id)
        actual = {int(row["chunk_index"]): row for row in rows}
        if set(actual) != set(mapping):
            raise RuntimeError(f"{paper_id} DB chunks do not match the audited fixture map")
        changed = 0
        for index, slot_keys in mapping.items():
            row = actual[index]
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            topic, current_slots = normalize_evidence_scope(payload)
            expected_slots = _expected_slots(slot_keys, inverse, paper_id, index)
            if topic == TOPIC_KEY and current_slots == expected_slots:
                summary["unchanged"] += 1
                continue
            changed += 1
            if not verify_only:
                ok = await sdb.update_chunk_evidence_scope(row["chunk_id"], TOPIC_KEY, expected_slots)
                if not ok:
                    raise RuntimeError(f"chunk disappeared during scope update: {row['chunk_id']}")
                summary["updated"] += 1
        if changed and not verify_only:
            indexed = await index_paper(paper_id)
            if indexed.get("status") != "ok":
                raise RuntimeError(f"{paper_id} Qdrant payload refresh failed: {indexed}")
            summary["papers"][paper_id] = indexed
        else:
            summary["papers"][paper_id] = {"status": "needs_update" if changed else "exact"}
    return summary


def _document_stream(reader, paper_id: str, pages: tuple[int, ...]) -> list[dict]:
    stream = []
    for page_number in pages:
        if page_number < 1 or page_number > len(reader):
            raise RuntimeError(f"{paper_id} page {page_number} is outside the {len(reader)}-page PDF")
        text = (reader[page_number - 1].get_text("text") or "").strip()
        if len(text) < 500:
            raise RuntimeError(f"{paper_id} page {page_number} has insufficient native text")
        stream.append({
            "block_id": f"{paper_id}:pdf-page:{page_number}",
            "page_num": page_number,
            "obj_index": 0,
            "type": "Text",
            "text": text,
        })
    return stream


async def _ingest_slices(
    reader, verify_only: bool, force: bool, inverse: dict[tuple[str, int], list[str]],
) -> list[dict]:
    results = []
    for spec in SLICE_SPECS:
        paper_id = spec["paper_id"]
        already_indexed = await sdb.all_chunks_indexed(paper_id)
        if already_indexed and not force:
            rows = await sdb.get_chunks_for_paper(paper_id)
            changed = []
            for row in rows:
                payload = json.loads(row.get("payload_json") or "{}")
                topic, current_slots = normalize_evidence_scope(payload)
                expected_slots = _expected_slots(spec["slot_keys"], inverse, paper_id, int(row["chunk_index"]))
                if topic != TOPIC_KEY or current_slots != expected_slots:
                    changed.append((row["chunk_id"], expected_slots))
            if changed and not verify_only:
                for chunk_id, expected_slots in changed:
                    if not await sdb.update_chunk_evidence_scope(chunk_id, TOPIC_KEY, expected_slots):
                        raise RuntimeError(f"chunk disappeared during scope update: {chunk_id}")
                indexed = await index_paper(paper_id)
                if indexed.get("status") != "ok":
                    raise RuntimeError(f"{paper_id} Qdrant payload refresh failed: {indexed}")
            results.append({
                "paper_id": paper_id,
                "status": "exact" if not changed or not verify_only else "scope_mismatch",
                "chunks": len(rows),
                "scope_updates": 0 if verify_only else len(changed),
            })
            continue
        if verify_only:
            results.append({"paper_id": paper_id, "status": "missing"})
            continue
        stream = _document_stream(reader, paper_id, spec["pages"])
        await sdb.upsert_paper(
            paper_id,
            SOURCE_PDF.name,
            str(SOURCE_PDF),
            status="core0_done",
            total_pages=len(spec["pages"]),
        )
        await sdb.update_paper_ocr(paper_id, {
            "paper_id": paper_id,
            "filename": SOURCE_PDF.name,
            "total_pages": len(spec["pages"]),
            "source_page_numbers": list(spec["pages"]),
            "extraction_status": "native_text_selected_pages",
            "document_stream": stream,
        })
        chunks = await run_ingestion(paper_id, stream, SOURCE_PDF.name, external_meta=_external_meta(spec["slot_keys"]))
        for chunk in chunks:
            expected_slots = _expected_slots(spec["slot_keys"], inverse, paper_id, int(chunk["chunk_index"]))
            if expected_slots != _dedupe(spec["slot_keys"]):
                if not await sdb.update_chunk_evidence_scope(chunk["chunk_id"], TOPIC_KEY, expected_slots):
                    raise RuntimeError(f"chunk disappeared during scope update: {chunk['chunk_id']}")
        embedded = await run_embedding_pipeline(paper_id)
        if embedded.get("status") != "ok" or embedded.get("embedded", 0) != len(chunks):
            raise RuntimeError(f"{paper_id} embedding failed: {embedded}")
        indexed = await index_paper(paper_id)
        if indexed.get("status") != "ok" or indexed.get("indexed", 0) != len(chunks):
            raise RuntimeError(f"{paper_id} indexing failed: {indexed}")
        results.append({
            "paper_id": paper_id,
            "status": "indexed_ready",
            "pages": list(spec["pages"]),
            "chunks": len(chunks),
            "embedding": embedded,
            "index": indexed,
        })
    return results


async def _sync_source_policies(verify_only: bool) -> dict:
    policies = load_source_policy_registry()
    metadata_by_paper = await sdb.get_paper_metadata(list(policies))
    missing = sorted(set(policies).difference(metadata_by_paper))
    if missing:
        raise RuntimeError(f"source policy references missing papers: {', '.join(missing)}")
    updates = 0
    chunk_updates = 0
    reindexed_papers = 0
    for paper_id, policy in policies.items():
        policy = validate_source_policy(policy)
        mirrored = {
            "source_lifecycle_status": policy["lifecycle_status"],
            "source_license_status": policy["license_status"],
            "source_document_version": policy["document_version"],
        }
        rows = await sdb.get_chunks_for_paper(paper_id)
        chunk_mismatch = any(
            any(json.loads(row.get("payload_json") or "{}").get(key) != value for key, value in mirrored.items())
            for row in rows
        )
        if metadata_by_paper[paper_id].get("source_policy") == policy and not chunk_mismatch:
            continue
        updates += 1
        if not verify_only:
            chunk_updates += await sdb.sync_paper_source_policy(paper_id, policy)
            indexed = await index_paper(paper_id)
            if indexed.get("status") != "ok":
                raise RuntimeError(f"{paper_id} source policy Qdrant refresh failed: {indexed}")
            reindexed_papers += 1
    return {
        "status": "exact" if updates == 0 or not verify_only else "needs_sync",
        "papers": len(policies),
        "updates": 0 if verify_only else updates,
        "pending_updates": updates if verify_only else 0,
        "chunk_updates": chunk_updates,
        "reindexed_papers": reindexed_papers,
        "commercial_publication_allowed": sum(
            policy["commercial_publication_allowed"] is True for policy in policies.values()
        ),
    }


async def run(verify_only: bool, force: bool) -> dict:
    affected_map, inverse = load_affected_slot_map()
    _validate_definitions(inverse)
    if not SOURCE_PDF.is_file():
        raise FileNotFoundError(SOURCE_PDF)
    await sdb.init_db()
    with fitz.open(str(SOURCE_PDF)) as reader:
        if len(reader) != 171:
            raise RuntimeError(f"audited PDF page count changed: expected 171, got {len(reader)}")
        legacy = await _backfill_legacy(verify_only, inverse)
        slices = await _ingest_slices(reader, verify_only, force, inverse)
    source_policies = await _sync_source_policies(verify_only)
    verified = (
        all(item.get("status") == "exact" for item in legacy["papers"].values())
        and all(item.get("status") == "exact" for item in slices)
        and source_policies["status"] == "exact"
    )
    return {
        "status": ("verified" if verified else "incomplete") if verify_only else "completed",
        "source_pdf": str(SOURCE_PDF),
        "affected_slot_map": {
            "mapping_version": affected_map["mapping_version"],
            "mapping_hash": affected_map["mapping_hash"],
            "slots": len(affected_map["slots"]),
            "generation_eligible": sum(item.get("generation_eligible") is True for item in affected_map["slots"]),
        },
        "legacy": legacy,
        "slices": slices,
        "source_policies": source_policies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare precise AF Topic evidence from the bundled public guideline fixture.")
    parser.add_argument("--verify-only", action="store_true", help="Read current state without changing DB, Qdrant, or calling providers.")
    parser.add_argument("--force", action="store_true", help="Rebuild already-indexed slice papers after a reviewed definition change.")
    args = parser.parse_args()
    result = asyncio.run(run(args.verify_only, args.force))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.verify_only and result["status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
