# 模組定位: AF Buyer Demo evidence-scope review provenance 的受控套用與驗證 CLI。
# 主要責任: 載入版本化 8-slot review artifact，append-only 寫入當前 scope snapshot 並回報 current gate。
# 呼叫來源: Phase 2 migration、Phase 3 scope 對齊後重錄、release/cumulative verification。
# 輸入契約: 有效 review artifact、現有 RAG SQLite；只有 --apply 允許 schema/row mutation。
# 輸出契約: 單一 JSON receipt，區分 reviewed decision 與 current_approved scope，不呼叫 LLM/embedding。
# 安全邊界: 不輸出 chunk text、provider key 或 secret；wildcard/未核准 source 由 artifact validator 拒絕。
# 維護提醒: mapping 改變時新增 artifact/review batch；不可 UPDATE/DELETE 既有 evidence_scope_reviews。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys


EBM_RAG_ROOT = Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from rag_core.common import state_db as sdb
from rag_core.core1_ingestion.affected_slot_map import load_demo_scope_review


def _table_exists(database: Path) -> bool:
    if not database.is_file():
        return False
    with sqlite3.connect(database) as conn:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_scope_reviews'"
        ).fetchone())


async def _run(args: argparse.Namespace) -> dict:
    review = load_demo_scope_review(args.review_artifact)
    database = args.database.resolve()
    sdb.RAG_DB_PATH = str(database)
    recorded = None
    if args.apply:
        await sdb.init_db()
        recorded = await sdb.record_evidence_scope_review_batch(review)
    elif not _table_exists(database):
        return {
            "status": "not_applied",
            "database": str(database),
            "review_hash": review["review_hash"],
            "reviewed": 0,
            "current_approved": 0,
            "pending": len(review["slots"]),
            "reason": "evidence_scope_reviews table is missing; rerun with --apply after backup",
        }

    slot_keys = [item["slot_key"] for item in review["slots"]]
    statuses = await sdb.get_evidence_scope_review_statuses(review["topic_key"], slot_keys)
    current = sum(1 for item in statuses.values() if item["current_approved"])
    reviewed = sum(1 for item in statuses.values() if item["reviewed"])
    receipt = {
        "status": "verified" if current == len(slot_keys) else "reviewed_with_scope_gaps",
        "database": str(database),
        "review_batch_id": review["review_batch_id"],
        "review_hash": review["review_hash"],
        "reviewed": reviewed,
        "current_approved": current,
        "pending": len(slot_keys) - current,
        "recorded": recorded,
        "slots": [statuses[slot_key] for slot_key in slot_keys],
    }
    if args.require_all_current and current != len(slot_keys):
        raise RuntimeError(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply or verify AF demo scope review provenance.")
    parser.add_argument(
        "--database",
        type=Path,
        default=EBM_RAG_ROOT / "data" / "sys" / "database" / "rag_state.db",
    )
    parser.add_argument(
        "--review-artifact",
        type=Path,
        default=(
            EBM_RAG_ROOT / "rag_core" / "core1_ingestion"
            / "atrial_fibrillation_demo_scope_review.v1.json"
        ),
    )
    parser.add_argument("--apply", action="store_true", help="Create schema and append current scope observations.")
    parser.add_argument("--require-all-current", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
