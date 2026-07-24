# 模組定位: 經人工核准的參考版型能力匯入 preview/apply CLI。
# 主要責任: 將 sanitized comparison 收斂成能力 plan，並以 SQL transaction 留下零 hierarchy 變更的稽核 receipt。
# 呼叫來源: Phase 三人工 review 後的開發期命令；不屬於疾病頁 runtime。
# 輸入契約: comparison JSON、核准能力、topic/manifest hash；apply 時使用既有 SidebarDatabase。
# 輸出契約: preview 或 applied receipt JSON，包含 plan/slot digest，不含來源標題、內容、URL、DOM 或 screenshot。
# 安全邊界: 只允許 model allowlist 的一般 UI 能力；apply 前後 slot identity 必須逐字一致。
# 維護提醒: 真正 hierarchy 匯入需另開 identity migration，不得擴充此 audit-only 路徑偷渡節點。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

from app.model.sidebar_model import SidebarDatabase
from tools.reference_layout_audit import build_reference_import_plan


RECEIPT_SCHEMA = "llmebm-reference-import-receipt.v1"


def _load_comparison(path):
    path = Path(path)
    if path.stat().st_size > 256 * 1024:
        raise ValueError(f"Comparison exceeds 256 KiB: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def execute_reference_import(
    comparison,
    approved_capabilities,
    *,
    apply=False,
    topic_name="",
    topic_uid="",
    manifest_hash="",
    db_path="",
    expected_slot_count=None,
):
    """Return a deterministic preview, or atomically persist the reviewed audit when apply=True."""
    plan = build_reference_import_plan(comparison, approved_capabilities)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "plan": plan,
        "execution": {
            "status": "preview",
            "database_mutation": False,
            "hierarchy_mutation": False,
        },
    }
    if not apply:
        return receipt
    if not topic_name or not manifest_hash:
        raise ValueError("topic_name and manifest_hash are required for apply.")

    db = SidebarDatabase(db_path) if db_path else SidebarDatabase()
    before = db.reference_import_snapshot(topic_name, topic_uid)
    if expected_slot_count is not None and before["slot_count"] != expected_slot_count:
        raise ValueError(
            f"slot count changed before apply: expected {expected_slot_count}, got {before['slot_count']}."
        )
    audit = db.record_reference_import(
        topic_name=topic_name,
        topic_uid=topic_uid,
        comparison_hash=plan["comparison_hash"],
        plan_hash=plan["plan_hash"],
        approved_capabilities=plan["approved_capabilities"],
        expected_slot_ids_sha256=before["slot_ids_sha256"],
        manifest_hash=manifest_hash,
    )
    after = db.reference_import_snapshot(topic_name, topic_uid)
    if before != after:
        raise RuntimeError("slot identity changed during reference import audit.")
    receipt["execution"] = {
        "status": audit["status"],
        "database_mutation": audit["status"] == "applied",
        "hierarchy_mutation": False,
        "topic_uid": audit["topic_uid"],
        "manifest_hash": manifest_hash,
        "slot_count": after["slot_count"],
        "slot_ids_sha256": after["slot_ids_sha256"],
        "slot_identity_unchanged": True,
    }
    return receipt


def main():
    parser = argparse.ArgumentParser(description="Preview or apply an approved capability-only import.")
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--approved-capability", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--topic", default="")
    parser.add_argument("--topic-uid", default="")
    parser.add_argument("--manifest-hash", default="")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--expected-slot-count", type=int)
    args = parser.parse_args()

    receipt = execute_reference_import(
        _load_comparison(args.comparison),
        args.approved_capability,
        apply=args.apply,
        topic_name=args.topic,
        topic_uid=args.topic_uid,
        manifest_hash=args.manifest_hash,
        db_path=args.db_path,
        expected_slot_count=args.expected_slot_count,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({
        "status": receipt["execution"]["status"],
        "plan_hash": receipt["plan"]["plan_hash"],
        "slot_identity_unchanged": receipt["execution"].get("slot_identity_unchanged"),
        "output": str(output.resolve()),
    }))


if __name__ == "__main__":
    main()
