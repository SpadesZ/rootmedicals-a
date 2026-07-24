# 模組定位: llmebm 動態 Topic Page 的最小 stdlib contract suite。
# 主要責任: 鎖定 manifest/slot identity、內容版本、evidence stale、單-slot生成 guard、掃描 artifact 與安全 renderer。
# 呼叫來源: 開發者本機 unittest discover 與 Phase 驗收命令。
# 輸入契約: temporary SQLite、fixture manifest/content 與本機 source files。
# 輸出契約: 無網路、無外部 LLM 的 deterministic pass/fail。
# 安全邊界: 不讀 production DB；所有寫入只在 TemporaryDirectory。
# 維護提醒: 每個非平凡 contract 至少保留一個會在退化時失敗的測試。
# ----------------------------------------------------------------------------------------------------

import json
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

IMPORT_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["LLMEBM_TOPIC_CONTENT_DB_PATH"] = str(Path(IMPORT_TEMP_DIR.name) / "import-topic.db")
os.environ["LLMEBM_TOPIC_SCAN_ROOT"] = str(Path(IMPORT_TEMP_DIR.name) / "scans")
os.environ["LLMEBM_SIDEBAR_DB_PATH"] = str(Path(IMPORT_TEMP_DIR.name) / "import-sidebar.db")

from app.model.sidebar_model import MIGRATIONS_DIR, SidebarDatabase, canonical_topic_slug
from app.model.topic_content_model import (
    TopicContentDatabase,
    build_manifest,
    load_screenshot_payloads,
    manifest_hash,
    slot_structure_revision,
    validate_content,
    validate_evidence_revision,
)
from tools.scan_topic_manifest import (
    canonicalize_dom_slots,
    classify_scan_status,
    should_trigger_generation,
    validate_scan_url,
)
from tools.reference_layout_audit import (
    build_reference_comparison,
    build_reference_import_plan,
    validate_reference_comparison,
    validate_structural_capture,
)
from tools.reference_structure_import import execute_reference_import
from tools.generate_topic_content_batches import build_batches, restrict_candidates, select_generation_candidates
from app.topic_security import (
    development_review_session_enabled,
    generation_request_authorized,
    validate_review_generation_payload,
    validate_review_generation_status,
)


class SidebarSlotContractTests(unittest.TestCase):
    STABLE_UNIVERSAL_KEYS = (
        "u1", "u1-1", "u1-2",
        "u2", "u2-1", "u2-2", "u2-2-1",
        "u3", "u3-1", "u3-2", "u3-2-1", "u3-2-2",
        "u4", "u4-1", "u4-2", "u4-2-1", "u4-2-2",
        "u5", "u5-1", "u5-2", "u5-2-1",
    )

    @staticmethod
    def slot_ids(tree):
        result = []

        def walk(nodes):
            for node in nodes:
                result.append(node["slot_id"])
                walk(node.get("children", []))

        walk(tree["universal"])
        walk(tree["custom"])
        return result

    def test_topic_aliases_share_canonical_slug_and_uid(self):
        self.assertEqual(canonical_topic_slug("Atrial Fibrillation"), "atrial-fibrillation")
        self.assertEqual(
            SidebarDatabase.topic_uid_for("Atrial Fibrillation"),
            SidebarDatabase.topic_uid_for("atrial-fibrillation"),
        )

    def test_taxonomy_is_checksum_locked_sql_and_not_python_runtime_data(self):
        source = (MIGRATIONS_DIR.parent / "sidebar_model.py").read_text(encoding="utf-8")
        self.assertNotIn("UNIVERSAL_TEMPLATE", source)
        self.assertNotIn("ATRIAL_FIBRILLATION_REFERENCE_TAXONOMY", source)
        migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
        self.assertEqual([path.name for path in migrations], [
            "001_condition_hierarchy_template.sql",
            "002_atrial_fibrillation_hierarchy.sql",
            "003_reviewed_heading_corrections.sql",
        ])
        for path in migrations:
            header = path.read_text(encoding="utf-8").splitlines()[:7]
            self.assertEqual(len(header), 7)
            self.assertTrue(all(line.startswith("-- ") for line in header))

        with tempfile.TemporaryDirectory() as temp_dir:
            db = SidebarDatabase(str(Path(temp_dir) / "sidebar.db"))
            with db.get_connection() as conn:
                versions = [row["version"] for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )]
                self.assertEqual(versions, [path.stem for path in migrations])
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM sidebar_template_nodes WHERE template_key = 'condition.v1'"
                ).fetchone()[0], 21)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM sidebar_topic_seed_nodes WHERE topic_uid = ?",
                    (db.topic_uid_for("atrial-fibrillation"),),
                ).fetchone()[0], 121)
            af_uid = db.topic_uid_for("atrial-fibrillation")
            af_tree = db.get_sidebar_tree("atrial-fibrillation", af_uid)
            self.assertEqual(len(self.slot_ids(af_tree)), 121)
            self.assertEqual(
                build_manifest("atrial-fibrillation", af_uid, af_tree)["dom_hash"],
                "sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6",
            )

    def test_applied_migration_checksum_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sidebar.db"
            db = SidebarDatabase(str(path))
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
                    ("sha256:" + "0" * 64, "001_condition_hierarchy_template"),
                )
                conn.commit()
            with self.assertRaisesRegex(RuntimeError, "checksum changed"):
                SidebarDatabase(str(path))

    def test_restart_reads_edited_sql_row_without_runtime_taxonomy_correction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sidebar.db"
            db = SidebarDatabase(str(path))
            topic_uid = db.topic_uid_for("atrial-fibrillation")
            before_ids = self.slot_ids(db.get_sidebar_tree("atrial-fibrillation", topic_uid))
            with db.get_connection() as conn:
                conn.execute(
                    """UPDATE sidebar_nodes SET name = 'Locally Reviewed Diagnosis'
                       WHERE topic_uid = ? AND source = 'universal' AND node_key = 'u3'""",
                    (topic_uid,),
                )
                conn.commit()
            restarted = SidebarDatabase(str(path))
            tree = restarted.get_sidebar_tree("atrial-fibrillation", topic_uid)
            self.assertEqual(self.slot_ids(tree), before_ids)
            self.assertEqual(
                next(node for node in tree["universal"] if node["id"] == "u3")["name"],
                "Locally Reviewed Diagnosis",
            )

    def test_allowed_blocks_flow_from_sql_row_into_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SidebarDatabase(str(Path(temp_dir) / "sidebar.db"))
            topic_uid = db.topic_uid_for("AAA")
            db.get_sidebar_tree("AAA", topic_uid)
            with db.get_connection() as conn:
                conn.execute(
                    """UPDATE sidebar_nodes SET allowed_blocks_json = '["summary"]'
                       WHERE topic_uid = ? AND source = 'universal' AND node_key = 'u1'""",
                    (topic_uid,),
                )
                conn.commit()
            manifest = build_manifest("aaa", topic_uid, db.get_sidebar_tree("AAA", topic_uid))
            root = next(slot for slot in manifest["slots"] if slot["slot_id"].endswith(":u1"))
            child = next(slot for slot in manifest["slots"] if slot["slot_id"].endswith(":u1-1"))
            self.assertEqual(root["allowed_blocks"], ["summary"])
            self.assertIn("bullets", child["allowed_blocks"])

    def test_reference_import_audit_is_idempotent_and_never_moves_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SidebarDatabase(str(Path(temp_dir) / "sidebar.db"))
            before_tree = db.get_sidebar_tree("AAA", "AAA-UID")
            before_ids = self.slot_ids(before_tree)
            snapshot = db.reference_import_snapshot("AAA", "AAA-UID")
            kwargs = {
                "topic_name": "AAA",
                "topic_uid": "AAA-UID",
                "comparison_hash": "sha256:" + "a" * 64,
                "plan_hash": "sha256:" + "b" * 64,
                "approved_capabilities": ["interactive_topic_tabs", "back_to_top"],
                "expected_slot_ids_sha256": snapshot["slot_ids_sha256"],
                "manifest_hash": "sha256:" + "c" * 64,
            }

            first = db.record_reference_import(**kwargs)
            second = db.record_reference_import(**kwargs)

            self.assertEqual(first["status"], "applied")
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(first["slot_count"], 21)
            self.assertEqual(self.slot_ids(db.get_sidebar_tree("AAA", "AAA-UID")), before_ids)
            with db.get_connection() as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM reference_structure_imports").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM sidebar_nodes WHERE source NOT IN ('universal', 'custom')"
                    ).fetchone()[0],
                    0,
                )

            rejected = dict(kwargs)
            rejected["plan_hash"] = "sha256:" + "d" * 64
            rejected["expected_slot_ids_sha256"] = "sha256:" + "e" * 64
            with self.assertRaisesRegex(ValueError, "slot identity changed"):
                db.record_reference_import(**rejected)
            with db.get_connection() as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM reference_structure_imports").fetchone()[0],
                    1,
                )

    def test_custom_slot_id_survives_heading_rename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SidebarDatabase(str(Path(temp_dir) / "sidebar.db"))
            node_id = db.add_custom_node("AAA", None, "Old heading")
            before = db.get_sidebar_tree("AAA", "AAA-UID")["custom"][0]
            with db.get_connection() as conn:
                conn.execute("UPDATE sidebar_nodes SET name = ? WHERE id = ?", ("New heading", node_id))
                conn.commit()
            after = db.get_sidebar_tree("AAA", "AAA-UID")["custom"][0]
            self.assertEqual(before["slot_id"], after["slot_id"])
            self.assertEqual(after["slot_id"], f"AAA-UID:custom:{node_id}")
            self.assertTrue(after["content_target"])

    def test_universal_and_custom_namespaces_do_not_collide(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SidebarDatabase(str(Path(temp_dir) / "sidebar.db"))
            db.add_custom_node("AAA", None, "Overview and Recommendations")
            tree = db.get_sidebar_tree("AAA", "AAA-UID")
            self.assertNotEqual(tree["universal"][0]["slot_id"], tree["custom"][0]["slot_id"])
            with db.get_connection() as conn:
                universal_id = conn.execute(
                    "SELECT id FROM sidebar_nodes WHERE source = 'universal' ORDER BY id LIMIT 1"
                ).fetchone()["id"]
            with self.assertRaisesRegex(ValueError, "not found"):
                db.delete_custom_node(universal_id)
            with self.assertRaisesRegex(ValueError, "Parent ID not found"):
                db.add_custom_node("AAA", universal_id, "Cross-source child")

    def test_legacy_migration_preserves_exact_slot_ids_and_six_ready_rows(self):
        """Gate SQL migration on identity, manifest hash, current content, and rerun idempotence."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sidebar_path = root / "sidebar.db"
            topic_path = root / "topic.db"
            with closing(sqlite3.connect(sidebar_path)) as conn:
                conn.executescript("""
                    CREATE TABLE sidebar_nodes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        topic_name TEXT NOT NULL,
                        parent_id INTEGER,
                        name TEXT NOT NULL,
                        layer_level INTEGER NOT NULL,
                        FOREIGN KEY (parent_id) REFERENCES sidebar_nodes (id)
                    );
                    INSERT INTO sidebar_nodes (topic_name, parent_id, name, layer_level)
                    VALUES ('AAA', NULL, 'Legacy root', 1);
                    INSERT INTO sidebar_nodes (topic_name, parent_id, name, layer_level)
                    VALUES ('AAA', 1, 'Legacy child', 2);
                """)

            topic_uid = SidebarDatabase.topic_uid_for("AAA")
            template_tree = SidebarDatabase(str(root / "template.db")).get_sidebar_tree(
                "AAA", topic_uid
            )
            before_tree = {
                "universal": template_tree["universal"],
                "custom": [SidebarDatabase._with_slot_metadata({
                    "id": 1,
                    "name": "Legacy root",
                    "layer": 1,
                    "children": [{
                        "id": 2, "name": "Legacy child", "layer": 2, "children": [],
                    }],
                }, topic_uid, "custom")],
            }
            before_slot_ids = self.slot_ids(before_tree)
            self.assertEqual(
                before_slot_ids[:21],
                [f"{topic_uid}:universal:{key}" for key in self.STABLE_UNIVERSAL_KEYS],
            )
            before_manifest = build_manifest("aaa", topic_uid, before_tree)

            content_db = TopicContentDatabase(str(topic_path))
            for slot_id in before_slot_ids[:6]:
                content_db.save_section_result(topic_uid, before_manifest["dom_hash"], {
                    "slot_id": slot_id,
                    "content": {
                        "schema": "llmebm-topic-content.v1",
                        "slot_id": slot_id,
                        "status": "ready",
                        "blocks": [{"type": "summary", "text": "fixture", "citations": []}],
                        "missing_evidence": [],
                        "model": {"provider": "fixture", "model_id": "fixture"},
                    },
                })
            with content_db.get_connection() as conn:
                before_content_rows = [tuple(row) for row in conn.execute(
                    """SELECT id, slot_id, manifest_hash, status, is_current, updated_at
                       FROM topic_content_versions ORDER BY id"""
                ).fetchall()]

            migrated = SidebarDatabase(str(sidebar_path))
            after_tree = migrated.get_sidebar_tree("AAA", topic_uid)
            after_manifest = build_manifest("aaa", topic_uid, after_tree)
            self.assertEqual(self.slot_ids(after_tree), before_slot_ids)
            self.assertEqual(after_manifest["dom_hash"], before_manifest["dom_hash"])

            with migrated.get_connection() as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(sidebar_nodes)")}
                self.assertTrue({
                    "topic_uid", "node_key", "source", "sort_order", "content_target",
                    "allowed_blocks_json", "status", "created_at", "updated_at",
                }.issubset(columns))
                legacy_rows = [tuple(row) for row in conn.execute(
                    """SELECT id, node_key, source FROM sidebar_nodes
                       WHERE source = 'custom' ORDER BY id"""
                ).fetchall()]
                self.assertEqual(legacy_rows, [(1, "1", "custom"), (2, "2", "custom")])
                row_count = conn.execute("SELECT COUNT(*) FROM sidebar_nodes").fetchone()[0]
                self.assertEqual(row_count, 23)

            rerun_tree = SidebarDatabase(str(sidebar_path)).get_sidebar_tree("AAA", topic_uid)
            self.assertEqual(self.slot_ids(rerun_tree), before_slot_ids)
            with migrated.get_connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM sidebar_nodes").fetchone()[0], row_count)

            with content_db.get_connection() as conn:
                after_content_rows = [tuple(row) for row in conn.execute(
                    """SELECT id, slot_id, manifest_hash, status, is_current, updated_at
                       FROM topic_content_versions ORDER BY id"""
                ).fetchall()]
            self.assertEqual(after_content_rows, before_content_rows)
            statuses = content_db.status_for_topic(topic_uid, after_manifest)["slots"]
            self.assertEqual(
                [statuses[slot_id]["status"] for slot_id in before_slot_ids[:6]],
                ["ready"] * 6,
            )

    def test_legacy_ada_heading_migration_preserves_slot_and_stales_only_that_slot(self):
        """Gate the semantic migration on exact identity and per-slot invalidation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sidebar_path = root / "sidebar.db"
            content_db = TopicContentDatabase(str(root / "topic.db"))
            db = SidebarDatabase(str(sidebar_path))
            topic_uid = db.topic_uid_for("atrial-fibrillation")
            db.get_sidebar_tree("atrial-fibrillation", topic_uid)
            with db.get_connection() as conn:
                conn.execute(
                    """UPDATE sidebar_nodes SET name = 'ADA Guidelines'
                       WHERE topic_uid = ? AND source = 'universal' AND node_key = 'u5-2-1'""",
                    (topic_uid,),
                )
                conn.execute(
                    """DELETE FROM schema_migrations
                       WHERE version IN ('002_atrial_fibrillation_hierarchy',
                                         '003_reviewed_heading_corrections')"""
                )
                conn.commit()
                rows = conn.execute(
                    """SELECT id, parent_id, node_key, source, name, layer_level, content_target,
                              allowed_blocks_json
                       FROM sidebar_nodes WHERE topic_uid = ? AND source = 'universal'
                       ORDER BY sort_order, id""",
                    (topic_uid,),
                ).fetchall()
            before_tree = {
                "topic_uid": topic_uid,
                "universal": [
                    db._with_slot_metadata(node, topic_uid, "universal")
                    for node in db._tree_from_rows(rows, "universal")
                ],
                "custom": [],
            }
            before_manifest = content_db.save_manifest(
                build_manifest("atrial-fibrillation", topic_uid, before_tree)
            )
            target_slot = f"{topic_uid}:universal:u5-2-1"
            control_slot = f"{topic_uid}:universal:u5-2"
            for slot_id in (target_slot, control_slot):
                content_db.save_section_result(topic_uid, before_manifest["dom_hash"], {
                    "slot_id": slot_id,
                    "content": {
                        "schema": "llmebm-topic-content.v1",
                        "slot_id": slot_id,
                        "status": "ready",
                        "blocks": [{"type": "summary", "text": "fixture", "citations": []}],
                        "missing_evidence": [],
                        "model": {"provider": "fixture", "model_id": "fixture"},
                    },
                })

            migrated = SidebarDatabase(str(sidebar_path))
            after_tree = migrated.get_sidebar_tree("atrial-fibrillation", topic_uid)
            after_manifest = build_manifest("atrial-fibrillation", topic_uid, after_tree)
            target = next(slot for slot in after_manifest["slots"] if slot["slot_id"] == target_slot)
            self.assertEqual(self.slot_ids(after_tree), self.slot_ids(before_tree))
            self.assertEqual(target["heading"], "United States Guidelines")
            statuses = content_db.status_for_topic(topic_uid, after_manifest)["slots"]
            self.assertEqual(statuses[target_slot]["status"], "stale")
            self.assertEqual(statuses[control_slot]["status"], "ready")

    def test_af_reference_taxonomy_migration_adds_slots_without_orphaning_existing_content(self):
        """Gate the full AF hierarchy as an additive migration without changing another disease."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sidebar_path = root / "sidebar.db"
            content_db = TopicContentDatabase(str(root / "topic.db"))
            db = SidebarDatabase(str(sidebar_path))
            topic_uid = db.topic_uid_for("atrial-fibrillation")
            db.get_sidebar_tree("atrial-fibrillation", topic_uid)
            # Recreate the accepted 21-slot baseline before the additive full-menu migration.
            with db.get_connection() as conn:
                rows_to_remove = conn.execute(
                    """SELECT id FROM sidebar_nodes
                       WHERE topic_uid = ? AND source = 'universal' AND node_key LIKE 'af-%'
                       ORDER BY layer_level DESC, id DESC""",
                    (topic_uid,),
                ).fetchall()
                for row in rows_to_remove:
                    conn.execute("DELETE FROM sidebar_nodes WHERE id = ?", (row["id"],))
                conn.commit()
            legacy = {
                "u3-2-1": ("Blood Tests", "u3-2", 3),
                "u3-2-2": ("Imaging", "u3-2", 3),
                "u4-2": ("Medications", "u4", 2),
                "u4-2-1": ("First-line therapies", "u4-2", 3),
                "u4-2-2": ("Alternative therapies", "u4-2", 3),
                "u5-1": ("International Guidelines", "u5", 2),
                "u5-2": ("United States Guidelines", "u5", 2),
                "u5-2-1": ("Professional Society Guidelines", "u5-2", 3),
            }
            with db.get_connection() as conn:
                ids = {
                    row["node_key"]: row["id"] for row in conn.execute(
                        """SELECT id, node_key FROM sidebar_nodes
                           WHERE topic_uid = ? AND source = 'universal'""",
                        (topic_uid,),
                    ).fetchall()
                }
                for node_key, (name, parent_key, layer) in legacy.items():
                    conn.execute(
                        """UPDATE sidebar_nodes SET name = ?, parent_id = ?, layer_level = ?
                           WHERE topic_uid = ? AND source = 'universal' AND node_key = ?""",
                        (name, ids[parent_key], layer, topic_uid, node_key),
                    )
                conn.execute(
                    """DELETE FROM schema_migrations
                       WHERE version IN ('002_atrial_fibrillation_hierarchy',
                                         '003_reviewed_heading_corrections')"""
                )
                conn.commit()
                rows = conn.execute(
                    """SELECT id, parent_id, node_key, source, name, layer_level, content_target,
                              allowed_blocks_json
                       FROM sidebar_nodes WHERE topic_uid = ? AND source = 'universal'
                       ORDER BY sort_order, id""",
                    (topic_uid,),
                ).fetchall()
            before_tree = {
                "topic_uid": topic_uid,
                "universal": [
                    db._with_slot_metadata(node, topic_uid, "universal")
                    for node in db._tree_from_rows(rows, "universal")
                ],
                "custom": [],
            }
            before_slot_ids = self.slot_ids(before_tree)
            before_manifest = content_db.save_manifest(
                build_manifest("atrial-fibrillation", topic_uid, before_tree)
            )
            for slot_id in before_slot_ids:
                content_db.save_section_result(topic_uid, before_manifest["dom_hash"], {
                    "slot_id": slot_id,
                    "content": {
                        "schema": "llmebm-topic-content.v1",
                        "slot_id": slot_id,
                        "status": "ready",
                        "blocks": [{"type": "summary", "text": "fixture", "citations": []}],
                        "missing_evidence": [],
                        "model": {"provider": "fixture", "model_id": "fixture"},
                    },
                })
            with content_db.get_connection() as conn:
                before_content_rows = [tuple(row) for row in conn.execute(
                    "SELECT * FROM topic_content_versions ORDER BY id"
                ).fetchall()]

            migrated = SidebarDatabase(str(sidebar_path))
            after_tree = migrated.get_sidebar_tree("atrial-fibrillation", topic_uid)
            after_manifest = build_manifest("atrial-fibrillation", topic_uid, after_tree)
            slots = {slot["slot_id"]: slot for slot in after_manifest["slots"]}
            after_slot_ids = self.slot_ids(after_tree)
            self.assertEqual(len(before_slot_ids), 21)
            self.assertEqual(len(after_slot_ids), 121)
            self.assertTrue(set(before_slot_ids).issubset(set(after_slot_ids)))
            expected = {
                "u3-2-1": ("Blood Tests", ["Diagnosis", "Blood Tests"], 2),
                "u3-2-2": (
                    "Transthoracic Echocardiogram (TTE)",
                    ["Diagnosis", "Transthoracic Echocardiogram (TTE)"],
                    2,
                ),
                "u4-2": ("Treatment Setting", ["Management", "Treatment Setting"], 2),
                "u4-2-1": ("Rate Control", ["Management", "Rate Control"], 2),
                "u4-2-2": ("Cardioversion", ["Management", "Cardioversion"], 2),
                "u5-1": ("Guidelines", ["Guidelines and Resources", "Guidelines"], 2),
                "u5-2": (
                    "International Guidelines",
                    ["Guidelines and Resources", "Guidelines", "International Guidelines"],
                    3,
                ),
                "u5-2-1": (
                    "United States Guidelines",
                    ["Guidelines and Resources", "Guidelines", "United States Guidelines"],
                    3,
                ),
            }
            for node_key, (heading, heading_path, level) in expected.items():
                slot = slots[f"{topic_uid}:universal:{node_key}"]
                self.assertEqual((slot["heading"], slot["heading_path"], slot["level"]), (
                    heading, heading_path, level,
                ))

            statuses = content_db.status_for_topic(topic_uid, after_manifest)["slots"]
            stale_slots = {
                slot_id for slot_id, value in statuses.items() if value["status"] == "stale"
            }
            self.assertEqual(stale_slots, {
                f"{topic_uid}:universal:u3-2-1",
                f"{topic_uid}:universal:u3-2-2",
                f"{topic_uid}:universal:u4-2",
                f"{topic_uid}:universal:u4-2-1",
                f"{topic_uid}:universal:u4-2-2",
                f"{topic_uid}:universal:u5-1",
                f"{topic_uid}:universal:u5-2",
                f"{topic_uid}:universal:u5-2-1",
            })
            new_slot_ids = set(after_slot_ids) - set(before_slot_ids)
            self.assertEqual(
                {statuses[slot_id]["status"] for slot_id in new_slot_ids}, {"empty"}
            )
            with content_db.get_connection() as conn:
                after_content_rows = [tuple(row) for row in conn.execute(
                    "SELECT * FROM topic_content_versions ORDER BY id"
                ).fetchall()]
            self.assertEqual(after_content_rows, before_content_rows)
            other_uid = migrated.topic_uid_for("type-1-diabetes")
            other_manifest = build_manifest(
                "type-1-diabetes", other_uid,
                migrated.get_sidebar_tree("type-1-diabetes", other_uid),
            )
            other_slots = {slot["slot_id"]: slot for slot in other_manifest["slots"]}
            self.assertEqual(other_slots[f"{other_uid}:universal:u4-2"]["heading"], "Medications")
            self.assertEqual(
                other_slots[f"{other_uid}:universal:u5-2"]["heading_path"],
                ["Guidelines and Resources", "United States Guidelines"],
            )
            self.assertEqual(
                self.slot_ids(SidebarDatabase(str(sidebar_path)).get_sidebar_tree(
                    "atrial-fibrillation", topic_uid
                )),
                after_slot_ids,
            )

    def test_sidebar_search_returns_stable_deep_link_and_treats_wildcards_literally(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SidebarDatabase(str(Path(temp_dir) / "sidebar.db"))
            tree = db.get_sidebar_tree("atrial-fibrillation")
            patient = next(
                result for result in db.search_topics_and_sections("Patient Information")
                if result["heading"] == "Patient Information"
            )
            expected = next(
                slot_id for slot_id in self.slot_ids(tree)
                if slot_id.endswith(":af-patient-information")
            )
            self.assertEqual(patient["slot_id"], expected)
            self.assertEqual(db.search_topics_and_sections("%"), [])

    def test_topic_user_state_is_durable_and_separate_from_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "topic.db"
            db = TopicContentDatabase(str(path))
            self.assertEqual(db.get_user_state("topic-1")["followed"], False)
            seen_hash = "sha256:" + "a" * 64
            state = db.set_user_state(
                "topic-1", followed=True, mark_seen=True, seen_status_hash=seen_hash
            )
            self.assertTrue(state["followed"])
            self.assertIsNotNone(state["last_seen_at"])
            self.assertEqual(state["last_seen_status_hash"], seen_hash)
            self.assertTrue(TopicContentDatabase(str(path)).get_user_state("topic-1")["followed"])
            with db.get_connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM topic_content_versions").fetchone()[0], 0)
            with self.assertRaisesRegex(ValueError, "requires seen_status_hash"):
                db.set_user_state("topic-1", mark_seen=True)


class ManifestContractTests(unittest.TestCase):
    def test_hash_ignores_viewport_screenshot_and_runtime_status(self):
        tree = {
            "universal": [{
                "id": "u1", "slot_id": "T:universal:u1", "name": "Overview", "layer": 1,
                "content_target": True, "content_status": "empty", "children": [],
            }],
            "custom": [],
        }
        first = build_manifest("Topic", "T", tree, {"width": 800, "height": 600}, [{"sha256": "a"}])
        tree["universal"][0]["content_status"] = "ready"
        second = build_manifest("Topic", "T", tree, {"width": 1920, "height": 1080}, [{"sha256": "b"}])
        self.assertEqual(first["dom_hash"], second["dom_hash"])

    def test_hash_changes_when_heading_changes(self):
        base = {
            "schema": "llmebm-topic-manifest.v1", "topic_uid": "T", "topic_name": "Topic",
            "template_version": "condition.v1", "slots": [{
                "slot_id": "T:universal:u1", "heading": "Before", "heading_path": ["Before"],
                "level": 1, "order": 0, "content_target": True, "allowed_blocks": ["summary"],
            }],
        }
        changed = json.loads(json.dumps(base))
        changed["slots"][0]["heading"] = "After"
        changed["slots"][0]["heading_path"] = ["After"]
        self.assertNotEqual(manifest_hash(base), manifest_hash(changed))

    def test_slot_structure_revision_ignores_layout_only_order(self):
        manifest = {
            "template_version": "condition.v1",
            "slots": [{
                "slot_id": "T:universal:u1",
                "heading": "Overview",
                "heading_path": ["Overview"],
                "level": 1,
                "order": 0,
                "content_target": True,
                "allowed_blocks": ["summary", "bullets"],
            }],
        }
        reordered = json.loads(json.dumps(manifest))
        reordered["slots"][0]["order"] = 20
        reordered["slots"][0]["allowed_blocks"].reverse()
        self.assertEqual(
            slot_structure_revision(manifest, manifest["slots"][0]),
            slot_structure_revision(reordered, reordered["slots"][0]),
        )

    def test_scanner_rejects_non_allowlisted_or_non_topic_url(self):
        allowed = ["http://127.0.0.1:33300"]
        self.assertEqual(
            validate_scan_url("http://127.0.0.1:33300/topic/AAA", allowed),
            "http://127.0.0.1:33300/topic/AAA",
        )
        with self.assertRaises(ValueError):
            validate_scan_url("http://169.254.169.254/topic/AAA", allowed)
        with self.assertRaises(ValueError):
            validate_scan_url("http://127.0.0.1:33300/admin", allowed)

    def test_dom_slot_canonicalization_deduplicates(self):
        slots = canonicalize_dom_slots([
            {"slot_id": "T:u:1", "heading": "A", "heading_path": ["A"], "level": 1},
            {"slot_id": "T:u:1", "heading": "A", "heading_path": ["A"], "level": 1},
        ])
        self.assertEqual(len(slots), 1)

    def test_unchanged_scan_does_not_trigger_generation(self):
        self.assertFalse(should_trigger_generation("unchanged"))
        self.assertTrue(should_trigger_generation("saved"))

    def test_visual_only_rescan_refreshes_screenshot_without_generation(self):
        current = {
            "dom_hash": "sha256:" + "a" * 64,
            "screenshots": [{"sha256": "b" * 64}],
        }
        rescanned = {
            "dom_hash": current["dom_hash"],
            "screenshots": [{"sha256": "c" * 64}],
        }
        status = classify_scan_status(current, rescanned, current_artifacts_valid=True)
        self.assertEqual(status, "visual_updated")
        self.assertFalse(should_trigger_generation(status))


class ReferenceLayoutContractTests(unittest.TestCase):
    @staticmethod
    def capture(
        source_family, *, search=0, fixed_header=False, article=1, aside=2,
        interactive_tabs=True,
    ):
        return {
            "schema": "reference-layout-capture.v1",
            "source_family": source_family,
            "viewport": {"width": 1280, "height": 720},
            "page": {
                "landmark_count": 4,
                "heading_count": 3,
                "heading_levels": [1, 2, 3],
                "article_count": article,
                "aside_count": aside,
                "section_count": 1,
                "table_count": 0,
                "list_count": 2,
            },
            "landmarks": [{
                "tag": "header",
                "role": "banner",
                "aria_label_present": False,
                "position": "fixed" if fixed_header else "static",
                "overflow_y": "visible",
            }],
            "interactions": {
                "total_controls_sampled": 20,
                "buttons": 8,
                "internal_links": 4,
                "external_links": 0,
                "search_inputs": search,
                "disclosure_controls": 3,
                "topic_disclosure_controls": 3,
                "expanded_true": 1,
                "controls_with_aria_controls": 3,
                "topic_controls_with_aria_controls": 3,
                "tablists": 1,
                "tabs": 4,
                "selected_tabs": 1,
                "details": 0,
                "back_to_top_controls": 0,
            },
            "accessibility": {
                "skip_links": 2,
                "controls_with_accessible_metadata": 20,
                "aria_live_regions": 1,
                "aria_busy_regions": 1,
                "current_markers": 0,
            },
            "screenshot": {
                "sha256": "a" * 64,
                "mime_type": "image/png",
                "state": "structural-review",
            },
            "state_graph": {
                "states": ["topic-default", "topic-secondary-tab", "topic-section-expanded"],
                "transitions": [
                    {
                        "action": "tab-select",
                        "from": "topic-default",
                        "to": "topic-secondary-tab" if interactive_tabs else "topic-default",
                        "verified": interactive_tabs,
                    },
                    {
                        "action": "section-disclosure-toggle",
                        "from": "topic-default",
                        "to": "topic-section-expanded",
                        "verified": True,
                    },
                ],
            },
        }

    def test_capture_rejects_text_identity_url_and_pixel_geometry(self):
        capture = self.capture("authenticated-medical-reference")
        for forbidden_key in ("text", "heading", "title", "url", "href", "html", "cookie", "brand", "box"):
            with self.subTest(key=forbidden_key):
                poisoned = json.loads(json.dumps(capture))
                poisoned["landmarks"][0][forbidden_key] = "must not persist"
                with self.assertRaisesRegex(ValueError, "unknown fields"):
                    validate_structural_capture(poisoned)

    def test_comparison_keeps_our_strengths_and_only_proposes_capabilities(self):
        current = self.capture(
            "llmebm-current", search=0, fixed_header=False, article=1, aside=2,
            interactive_tabs=False,
        )
        reference = self.capture(
            "authenticated-medical-reference", search=1, fixed_header=True, article=0, aside=0
        )
        comparison = build_reference_comparison(current, reference)
        candidates = {item["capability"]: item for item in comparison["capability_decisions"]}

        self.assertEqual(comparison["schema"], "llmebm-reference-layout-comparison.v1")
        self.assertTrue(comparison["approval_required"])
        self.assertFalse(comparison["production_mutation"])
        self.assertEqual(comparison["slot_identity_policy"], "preserve_existing_ids")
        self.assertEqual(candidates["global_search"]["decision"], "consider")
        self.assertEqual(candidates["fixed_global_header"]["decision"], "consider")
        self.assertEqual(candidates["interactive_topic_tabs"]["decision"], "consider")
        self.assertEqual(candidates["section_disclosure_state"]["decision"], "retain_shared")
        self.assertEqual(candidates["semantic_article"]["decision"], "retain_current")
        self.assertEqual(candidates["complementary_regions"]["decision"], "retain_current")
        serialized = json.dumps(comparison, ensure_ascii=False, sort_keys=True)
        for forbidden in ("must not persist", "medical_body", "source_html", "pixel_geometry"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(
            comparison["comparison_hash"],
            build_reference_comparison(current, reference)["comparison_hash"],
        )

    def test_reviewed_import_plan_keeps_only_approved_capabilities(self):
        current = self.capture(
            "llmebm-current", search=0, fixed_header=False, article=1, aside=2,
            interactive_tabs=False,
        )
        reference = self.capture(
            "authenticated-medical-reference", search=1, fixed_header=True, article=0, aside=0
        )
        reference["interactions"]["back_to_top_controls"] = 1
        comparison = build_reference_comparison(current, reference)

        self.assertEqual(validate_reference_comparison(comparison), comparison)
        tampered = json.loads(json.dumps(comparison))
        tampered["comparison_hash"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            validate_reference_comparison(tampered)
        plan = build_reference_import_plan(
            comparison, ["interactive_topic_tabs", "back_to_top"]
        )
        preview = execute_reference_import(
            comparison, ["interactive_topic_tabs", "back_to_top"]
        )

        self.assertEqual(plan["schema"], "llmebm-reference-import-plan.v1")
        self.assertEqual(preview["schema"], "llmebm-reference-import-receipt.v1")
        self.assertEqual(preview["execution"]["status"], "preview")
        self.assertFalse(preview["execution"]["database_mutation"])
        self.assertEqual(plan["review_status"], "approved")
        self.assertEqual(plan["import_mode"], "capabilities-only")
        self.assertEqual(
            plan["approved_capabilities"], ["interactive_topic_tabs", "back_to_top"]
        )
        self.assertEqual(plan["hierarchy_changes"], [])
        self.assertEqual(plan["external_data_retained"], [])
        self.assertFalse(plan["runtime_dependency"])
        self.assertEqual(plan["slot_identity_policy"], "preserve_existing_ids")
        self.assertEqual(
            plan["deferred_capabilities"], ["global_search", "fixed_global_header"]
        )
        self.assertRegex(plan["plan_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            plan["plan_hash"],
            build_reference_import_plan(
                comparison, ["interactive_topic_tabs", "back_to_top"]
            )["plan_hash"],
        )
        serialized = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "capture_evidence", "screenshot", "source_html", "heading", "url", "brand"
        ):
            self.assertNotIn(forbidden, serialized)

        with self.assertRaisesRegex(ValueError, "not reviewable candidates"):
            build_reference_import_plan(comparison, ["semantic_article"])


class PersistenceContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = TopicContentDatabase(str(Path(self.temp_dir.name) / "topic.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def content(text):
        return {
            "schema": "llmebm-topic-content.v1",
            "slot_id": "T:universal:u1",
            "status": "ready",
            "blocks": [{"type": "summary", "text": text, "citations": []}],
            "missing_evidence": [],
            "model": {"provider": "fixture", "model_id": "fixture"},
        }

    def test_failed_regeneration_preserves_last_ready_version(self):
        ready = {"slot_id": "T:universal:u1", "status": "ready", "content": self.content("v1")}
        self.db.save_section_result("T", "sha256:a", ready)
        self.db.save_section_result(
            "T", "sha256:a", {"slot_id": "T:universal:u1", "status": "failed", "error": "offline"}
        )
        current = self.db.get_current_content("T", "T:universal:u1")
        self.assertEqual(current["content"]["blocks"][0]["text"], "v1")

    def test_topic_status_exposes_bounded_current_version_metadata(self):
        self.db.save_section_result(
            "T", "sha256:a", {"slot_id": "T:universal:u1", "content": self.content("v1")}
        )
        status = self.db.status_for_topic("T")["slots"]["T:universal:u1"]
        self.assertEqual("ready", status["status"])
        self.assertEqual("generated", status["workflow_status"])
        self.assertIsInstance(status["version_id"], int)
        self.assertIsNone(status["reviewed_by"])
        self.assertIsNone(status["published_at"])

    def test_same_hash_rescan_repairs_manifest_screenshot_metadata(self):
        manifest = {
            "schema": "llmebm-topic-manifest.v1", "topic_uid": "T", "topic_name": "Topic",
            "template_version": "condition.v1", "screenshots": [], "slots": [{
                "slot_id": "T:universal:u1", "heading": "Overview", "heading_path": ["Overview"],
                "level": 1, "order": 0, "content_target": True, "allowed_blocks": ["summary"],
            }],
        }
        self.db.save_manifest(manifest)
        repaired = json.loads(json.dumps(manifest))
        repaired["screenshots"] = [{
            "artifact_path": "verification/scan.png", "sha256": "abc", "mime_type": "image/png"
        }]
        self.db.save_manifest(repaired)
        current = self.db.get_current_manifest("Topic")
        self.assertEqual(current["screenshots"][0]["sha256"], "abc")

    def test_resurfaced_manifest_hash_becomes_the_only_current_manifest(self):
        first = {
            "schema": "llmebm-topic-manifest.v1", "topic_uid": "T", "topic_name": "Topic",
            "template_version": "condition.v1", "slots": [{
                "slot_id": "T:universal:u1", "heading": "Before", "heading_path": ["Before"],
                "level": 1, "order": 0, "content_target": True, "allowed_blocks": ["summary"],
            }],
        }
        second = json.loads(json.dumps(first))
        second["slots"][0]["heading"] = "After"
        second["slots"][0]["heading_path"] = ["After"]
        self.db.save_manifest(first)
        self.db.save_manifest(second)
        self.db.save_manifest(first)
        current = self.db.get_current_manifest("Topic")
        self.assertEqual(current["dom_hash"], manifest_hash(first))
        with self.db.get_connection() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM topic_manifests WHERE topic_uid = 'T' AND status = 'current'"
                ).fetchone()[0],
                1,
            )

    def test_successful_regeneration_switches_current_atomically(self):
        self.db.save_section_result(
            "T", "sha256:a", {"slot_id": "T:universal:u1", "content": self.content("v1")}
        )
        self.db.save_section_result(
            "T", "sha256:b", {"slot_id": "T:universal:u1", "content": self.content("v2")}
        )
        current = self.db.get_current_content("T", "T:universal:u1")
        self.assertEqual(current["content"]["blocks"][0]["text"], "v2")
        with self.db.get_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM topic_content_versions WHERE is_current = 1").fetchone()[0], 1
            )

    def test_manifest_change_marks_current_content_stale(self):
        self.db.save_section_result(
            "T", "sha256:before", {"slot_id": "T:universal:u1", "content": self.content("v1")}
        )
        current_manifest = {
            "dom_hash": "sha256:after",
            "slots": [{"slot_id": "T:universal:u1"}],
        }
        status = self.db.status_for_topic("T", current_manifest)["slots"]["T:universal:u1"]
        self.assertEqual(status["status"], "stale")

    def test_heading_change_marks_only_affected_slot_stale_and_backfill_is_idempotent(self):
        first_slot = "T:universal:u1"
        second_slot = "T:universal:u2"
        before = {
            "schema": "llmebm-topic-manifest.v1",
            "topic_uid": "T",
            "topic_name": "Topic",
            "template_version": "condition.v1",
            "slots": [
                {
                    "slot_id": first_slot,
                    "heading": "First",
                    "heading_path": ["First"],
                    "level": 1,
                    "order": 0,
                    "content_target": True,
                    "allowed_blocks": ["summary"],
                },
                {
                    "slot_id": second_slot,
                    "heading": "Second",
                    "heading_path": ["Second"],
                    "level": 1,
                    "order": 1,
                    "content_target": True,
                    "allowed_blocks": ["summary"],
                },
            ],
        }
        before = self.db.save_manifest(before)
        for slot_id in (first_slot, second_slot):
            content = self.content(slot_id)
            content["slot_id"] = slot_id
            self.db.save_section_result(
                "T", before["dom_hash"], {"slot_id": slot_id, "content": content}
            )

        changed = json.loads(json.dumps(before))
        changed["slots"][0]["heading"] = "Renamed first"
        changed["slots"][0]["heading_path"] = ["Renamed first"]
        changed = self.db.save_manifest(changed)
        statuses = self.db.status_for_topic("T", changed)["slots"]
        self.assertEqual(statuses[first_slot]["status"], "stale")
        self.assertEqual(statuses[first_slot]["stale_reasons"], ["structure"])
        self.assertEqual(statuses[second_slot]["status"], "ready")
        self.assertEqual(statuses[second_slot]["stale_reasons"], [])

        with self.db.get_connection() as conn:
            revisions = [row["structure_revision"] for row in conn.execute(
                """SELECT structure_revision FROM topic_content_versions
                   WHERE topic_uid = 'T' AND is_current = 1 ORDER BY slot_id"""
            ).fetchall()]
            self.assertTrue(all(revisions))
            conn.execute(
                "UPDATE topic_content_versions SET structure_revision = NULL WHERE slot_id = ?",
                (second_slot,),
            )
            conn.commit()
        TopicContentDatabase(self.db.db_path)
        with self.db.get_connection() as conn:
            first_backfill = conn.execute(
                "SELECT structure_revision FROM topic_content_versions WHERE slot_id = ? AND is_current = 1",
                (second_slot,),
            ).fetchone()["structure_revision"]
        TopicContentDatabase(self.db.db_path)
        with self.db.get_connection() as conn:
            second_backfill = conn.execute(
                "SELECT structure_revision FROM topic_content_versions WHERE slot_id = ? AND is_current = 1",
                (second_slot,),
            ).fetchone()["structure_revision"]
        self.assertTrue(first_backfill)
        self.assertEqual(second_backfill, first_backfill)

    def test_evidence_revision_change_marks_current_content_stale(self):
        before_revision = "sha256:" + "a" * 64
        after_revision = "sha256:" + "b" * 64
        self.db.save_section_result(
            "T",
            "sha256:manifest",
            {
                "slot_id": "T:universal:u1",
                "content": self.content("v1"),
                "evidence_revision": before_revision,
            },
        )
        current_manifest = {
            "dom_hash": "sha256:manifest",
            "slots": [{"slot_id": "T:universal:u1"}],
        }
        unchanged = self.db.status_for_topic(
            "T", current_manifest, evidence_revision=before_revision
        )["slots"]["T:universal:u1"]
        changed = self.db.status_for_topic(
            "T", current_manifest, evidence_revision=after_revision
        )["slots"]["T:universal:u1"]
        self.assertEqual(unchanged["status"], "ready")
        self.assertEqual(unchanged["stale_reasons"], [])
        self.assertEqual(changed["status"], "stale")
        self.assertEqual(changed["stale_reasons"], ["evidence"])

    def test_scoped_evidence_revision_marks_only_changed_slot_stale(self):
        first_slot = "T:universal:u1"
        second_slot = "T:universal:u2"
        first_revision = "sha256:" + "a" * 64
        second_revision = "sha256:" + "b" * 64
        for slot_id, revision in ((first_slot, first_revision), (second_slot, second_revision)):
            content = self.content(slot_id)
            content["slot_id"] = slot_id
            self.db.save_section_result(
                "T",
                "sha256:manifest",
                {"slot_id": slot_id, "content": content, "evidence_revision": revision},
            )
        current_manifest = {
            "dom_hash": "sha256:manifest",
            "slots": [{"slot_id": first_slot}, {"slot_id": second_slot}],
        }
        statuses = self.db.status_for_topic(
            "T",
            current_manifest,
            evidence_revisions={
                first_slot: "sha256:" + "c" * 64,
                second_slot: second_revision,
            },
        )["slots"]

        self.assertEqual(statuses[first_slot]["status"], "stale")
        self.assertEqual(statuses[first_slot]["stale_reasons"], ["evidence"])
        self.assertEqual(statuses[second_slot]["status"], "ready")
        self.assertEqual(statuses[second_slot]["stale_reasons"], [])

    def test_invalid_evidence_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "sha256"):
            validate_evidence_revision("sha256:not-a-digest")

    def test_failed_section_persists_structured_error(self):
        error = {"stage": "retrieval", "error": "backend offline"}
        self.db.save_section_result(
            "T",
            "sha256:manifest",
            {"slot_id": "T:universal:u1", "status": "failed", "error": error},
        )
        with self.db.get_connection() as conn:
            stored = conn.execute(
                "SELECT error_json FROM topic_content_versions ORDER BY id DESC LIMIT 1"
            ).fetchone()["error_json"]
        self.assertEqual(json.loads(stored), error)

    def test_insufficient_regeneration_preserves_last_ready_version(self):
        self.db.save_section_result(
            "T", "sha256:a", {"slot_id": "T:universal:u1", "content": self.content("v1")}
        )
        insufficient = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": "T:universal:u1",
            "status": "insufficient_evidence",
            "blocks": [],
            "missing_evidence": ["updated guideline"],
            "model": {"provider": "none", "model_id": "none"},
        }
        self.db.save_section_result(
            "T", "sha256:b", {"slot_id": "T:universal:u1", "content": insufficient}
        )
        current = self.db.get_current_content("T", "T:universal:u1")
        self.assertEqual(current["status"], "ready")
        self.assertEqual(current["content"]["blocks"][0]["text"], "v1")

    def test_running_jobs_are_interrupted_on_restart(self):
        job_id = self.db.create_job("T", "sha256:a", ["T:universal:u1"])
        self.db.update_job(job_id, "running")
        restarted = TopicContentDatabase(self.db.db_path)
        status = restarted.status_for_topic("T")["latest_job"]
        self.assertEqual(status["status"], "interrupted")

    def test_invalid_component_type_is_rejected(self):
        payload = self.content("unsafe")
        payload["blocks"] = [{"type": "html", "html": "<script>alert(1)</script>"}]
        with self.assertRaises(ValueError):
            validate_content(payload, "T:universal:u1")

    def test_recommendation_grade_is_optional_but_bounded(self):
        payload = self.content("graded")
        payload["blocks"] = [{
            "type": "recommendations", "text": "Use the cited recommendation.",
            "citations": [], "recommendation_strength": "Strong",
            "evidence_certainty": "Moderate certainty",
        }]
        self.assertEqual(
            validate_content(payload, "T:universal:u1")["blocks"][0]["recommendation_strength"],
            "Strong",
        )
        payload["blocks"][0]["recommendation_strength"] = "x" * 81
        with self.assertRaisesRegex(ValueError, "short evidence-sourced label"):
            validate_content(payload, "T:universal:u1")


class ScreenshotArtifactContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.png = b"\x89PNG\r\n\x1a\nfixture"

    def tearDown(self):
        self.temp_dir.cleanup()

    def manifest(self, screenshot):
        return {"screenshots": [screenshot]}

    def test_missing_screenshot_requires_scan(self):
        with self.assertRaisesRegex(ValueError, "scan_required"):
            load_screenshot_payloads({"screenshots": []}, self.root)

    def test_artifact_path_cannot_escape_allowlisted_root(self):
        with self.assertRaisesRegex(ValueError, "escaped"):
            load_screenshot_payloads(self.manifest({
                "artifact_path": "../outside.png", "sha256": "x", "mime_type": "image/png"
            }), self.root)

    def test_artifact_hash_must_match(self):
        (self.root / "scan.png").write_bytes(self.png)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_screenshot_payloads(self.manifest({
                "artifact_path": "scan.png", "sha256": "wrong", "mime_type": "image/png"
            }), self.root)

    def test_verified_artifact_is_encoded_for_rag(self):
        (self.root / "scan.png").write_bytes(self.png)
        payloads = load_screenshot_payloads(self.manifest({
            "artifact_path": "scan.png", "sha256": hashlib.sha256(self.png).hexdigest(),
            "mime_type": "image/png", "state": "sidebar-expanded",
        }), self.root)
        self.assertEqual(payloads[0]["mime_type"], "image/png")
        self.assertNotIn("artifact_path", payloads[0])


class RendererContractTests(unittest.TestCase):
    def test_renderer_never_uses_inner_html(self):
        source = (LLMEBM_ROOT / "app" / "static" / "js" / "topic_content.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", source)
        self.assertIn("textContent", source)
        self.assertIn("createElement", source)
        self.assertIn("block.columns", source)
        self.assertIn("rowData.cells", source)

    def test_topic_draft_warning_matches_review_workflow(self):
        source = (LLMEBM_ROOT / "app" / "static" / "js" / "topic_content.js").read_text(encoding="utf-8")
        self.assertIn("Clinically reviewed draft — approved; publication pending.", source)
        self.assertIn("Internal generated draft — clinical review pending.", source)
        self.assertIn("Rejected draft — revision is required before review or publication.", source)
        self.assertIn("draftWarning(result)", source)

    def test_required_topic_api_routes_exist(self):
        source = (LLMEBM_ROOT / "main_ebm.py").read_text(encoding="utf-8")
        for suffix in ("/manifest", "/content/{slot_id}", "/content/{slot_id}/source-details", "/content/generate", "/content/status"):
            self.assertIn(f'/api/v1/topic/{{topic_name}}{suffix}', source)
        self.assertIn('set(content_paper_ids(stored["content"]))', source)

    def test_topic_views_are_real_panels_without_placeholder_tabs(self):
        template = (LLMEBM_ROOT / "app" / "templates" / "specialty.html").read_text(encoding="utf-8")
        self.assertIn('id="topic-trust-header"', template)
        self.assertIn('id="topic-trust-badge"', template)
        self.assertIn('id="topic-tab"', template)
        self.assertIn('aria-controls="topic-panel"', template)
        self.assertIn('id="updates-tab"', template)
        self.assertIn('aria-controls="updates-panel"', template)
        self.assertIn('role="tabpanel"', template)
        self.assertIn('id="btn-topic-back-to-top"', template)
        self.assertNotIn('role="tab" aria-selected="false">Images</button>', template)
        self.assertNotIn('role="tab" aria-selected="false">Tables</button>', template)
        renderer = (LLMEBM_ROOT / "app" / "static" / "js" / "topic_content.js").read_text(encoding="utf-8")
        self.assertIn("rootmedicals-source-details.v1", renderer)
        self.assertIn("Commercial publication", renderer)
        self.assertIn("renderTrustHeader", renderer)
        self.assertIn("Latest stored section changes", renderer)

    def test_topic_page_keeps_body_readable_at_narrow_viewports(self):
        template = (LLMEBM_ROOT / "app" / "templates" / "specialty.html").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 900px)", template)
        self.assertIn("#pane-left:not(.pane-hidden)", template)
        self.assertIn("window.matchMedia('(max-width: 900px)')", template)
        self.assertIn("min-width: 0", template)
        self.assertIn("overflow-wrap: anywhere", template)
        self.assertNotIn('<span class="topic-uid"', template)

    def test_public_navigation_has_no_dead_hash_routes(self):
        index = (LLMEBM_ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        topic = (LLMEBM_ROOT / "app" / "templates" / "specialty.html").read_text(encoding="utf-8")
        self.assertNotIn('href="#"', index)
        self.assertNotIn('href="#"', topic)
        self.assertIn('class="global-nav-disabled" aria-disabled="true"', index)
        self.assertIn('class="global-nav-disabled" aria-disabled="true"', topic)
        self.assertIn('<a href="/" class="active">Specialties</a>', index)
        self.assertIn('<a href="/" class="active">Specialties</a>', topic)

    def test_home_search_uses_owned_search_api_and_real_deep_links(self):
        index = (LLMEBM_ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        topic = (LLMEBM_ROOT / "app" / "templates" / "specialty.html").read_text(encoding="utf-8")
        source = (LLMEBM_ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/v1/search", source)
        self.assertIn("global-search-btn", source)
        self.assertIn("searchResultUrl", source)
        self.assertNotIn("${query} fibrillation", source)
        self.assertIn('/static/js/app.js?v=20260717-about-1', index)
        self.assertIn('/static/js/app.js?v=20260717-about-1', topic)

    def test_shared_about_button_opens_native_accessible_dialog(self):
        header = (LLMEBM_ROOT / "app" / "templates" / "_header.html").read_text(encoding="utf-8")
        script = (LLMEBM_ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
        css = (LLMEBM_ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
        for template_name in ("index.html", "specialty.html", "admin.html"):
            template = (LLMEBM_ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('/static/js/app.js?v=20260717-about-1', template)
            self.assertIn('/static/css/style.css?v=', template)
        self.assertIn('id="btn-about"', header)
        self.assertIn('aria-haspopup="dialog"', header)
        self.assertIn('id="about-dialog"', header)
        self.assertIn('aria-labelledby="about-dialog-title"', header)
        self.assertIn('id="btn-about-close"', header)
        self.assertIn("aboutDialog.showModal()", script)
        self.assertIn("aboutDialog.close()", script)
        self.assertIn('aria-expanded", "false', script)
        self.assertIn("aboutButton.focus()", script)
        self.assertIn(".about-dialog::backdrop", css)
        self.assertIn(".top-link-button:focus-visible", css)

    def test_home_has_direct_af_demo_entry_and_topic_defaults_to_overview(self):
        index = (LLMEBM_ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        renderer = (LLMEBM_ROOT / "app" / "static" / "js" / "topic_content.js").read_text(encoding="utf-8")
        self.assertIn('id="featured-buyer-demo"', index)
        self.assertIn('href="/topic/atrial-fibrillation"', index)
        self.assertIn("if (!selectDeepLinkedSlot()", renderer)
        self.assertIn("topicSlots[0]", renderer)

    def test_static_soap_demo_is_removed_and_tombstoned(self):
        source = (LLMEBM_ROOT / "main_ebm.py").read_text(encoding="utf-8")
        template = (LLMEBM_ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("pipeline_event_generator", source)
        self.assertNotIn("btn-analyze-soap", template)
        self.assertNotIn("stream_client.js", template)
        self.assertIn('status_code=410', source)

    def test_medical_review_admin_has_real_queue_detail_and_actions(self):
        source = (LLMEBM_ROOT / "main_ebm.py").read_text(encoding="utf-8")
        template = (LLMEBM_ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
        script = (LLMEBM_ROOT / "app" / "static" / "js" / "admin_review.js").read_text(encoding="utf-8")
        self.assertIn('/api/v1/topic/{topic_name}/review-queue', source)
        self.assertIn('/api/v1/topic/{topic_name}/content/{slot_id}/review-detail', source)
        self.assertIn('/api/v1/topic/{topic_name}/scope-reviews', source)
        self.assertIn('/api/v1/topic/{topic_name}/scope-reviews/{slot_id}/approve-current', source)
        self.assertIn('"all_items": all_items', source)
        self.assertIn('def _get_scope_review_statuses', source)
        self.assertIn('def _get_scope_review_status', source)
        self.assertIn('"scope_review": scope_review', source)
        self.assertIn('live_status = topic_content_db.status_for_topic', source)
        self.assertIn('live_status.get(item["slot_id"], {}).get("status") == "ready"', source)
        for element_id in (
            'tab-medical-review', 'review-token-input', 'review-topic-input', 'review-actor-input',
            'review-queue', 'review-draft', 'review-published', 'review-source-gate', 'review-history',
            'review-scope-review', 'review-scope-summary',
            'review-generation-slot', 'review-generate', 'review-generation-refresh', 'review-generation-job',
            'review-generation-reason', 'review-approve-mapping',
            'review-submit', 'review-approve', 'review-reject', 'review-publish', 'review-rollback',
        ):
            self.assertIn(f'id="{element_id}"', template)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("setAttribute('role', 'listitem')", script)
        self.assertIn("X-LLMEBM-Review-Token", script)
        self.assertIn("sessionStorage", script)
        self.assertIn("localStorage.getItem(REVIEW_TOKEN_STORAGE_KEY)", script)
        self.assertIn("localStorage.setItem(REVIEW_TOKEN_STORAGE_KEY, token)", script)
        self.assertIn('data-dev-review-session=', template)
        self.assertIn('placeholder="Development session active"', template)
        self.assertIn("function devReviewSessionEnabled", script)
        self.assertIn("if (token) headers['X-LLMEBM-Review-Token'] = token", script)
        self.assertIn("response.set_cookie(", source)
        self.assertIn("request.cookies.get(REVIEW_SESSION_COOKIE_NAME)", source)
        self.assertIn("function renderScopeReview", script)
        self.assertIn("function renderScopeReviewSummary", script)
        self.assertIn("renderScopeReviewSummary(scopePayload)", script)
        self.assertIn("renderScopeReview(payload.scope_review)", script)
        self.assertIn("only_slot_ids: [candidate.slotId]", script)
        self.assertIn("require_current_scope_review: true", script)
        self.assertIn("function approveCurrentMapping", script)
        self.assertIn("scopePayload.all_items", script)
        self.assertIn("/approve-current", script)
        self.assertIn("/content/generate", script)
        self.assertIn("latest_job", script)
        self.assertNotIn("X-LLMEBM-Topic-Token", script)

    def test_review_ui_generation_is_single_slot_non_forced_and_non_ready(self):
        self.assertEqual(
            validate_review_generation_payload(["topic:universal:u4-2-2"]),
            "topic:universal:u4-2-2",
        )
        self.assertEqual(validate_review_generation_status("stale"), "stale")
        for args in (
            ([], False, True),
            (["a", "b"], False, True),
            (["a"], True, True),
            (["a"], False, False),
        ):
            with self.assertRaises(ValueError):
                validate_review_generation_payload(
                    args[0], force=args[1], require_current_scope_review=args[2],
                )
        with self.assertRaises(ValueError):
            validate_review_generation_status("ready")


class DeliveryHeaderContractTests(unittest.TestCase):
    def test_topic_and_environment_files_declare_delivery_contract(self):
        repo_root = LLMEBM_ROOT.parent
        paths = (
            "README.md",
            "doc/LLMEBM_DYNAMIC_TOPIC_VERIFICATION_2026-07-13.md",
            "Use-Environment.ps1",
            "env-profiles/dev.env",
            "env-profiles/vm.env",
            "env-profiles/README.md",
            "llmebm/app/topic_security.py",
            "llmebm/app/medpilot.py",
            "llmebm/app/model/sidebar_model.py",
            "llmebm/app/model/topic_content_model.py",
            "llmebm/app/templates/index.html",
            "llmebm/app/templates/admin.html",
            "llmebm/app/templates/specialty.html",
            "llmebm/app/static/css/admin.css",
            "llmebm/app/static/css/style.css",
            "llmebm/app/static/js/admin_review.js",
            "llmebm/app/static/js/topic_content.js",
            "llmebm/tools/scan_topic_manifest.py",
            "llmebm/tools/reference_layout_audit.py",
            "llmebm/tools/reference_structure_import.py",
            "llmebm/tools/generate_topic_content_batches.py",
            "llmebm/tools/sqlite_backup.py",
            "llmebm/tests/test_topic_content_contract.py",
            "llmebm/tests/test_medpilot_contract.py",
            "llmebm/tests/topic_content_renderer_check.js",
            "llmebm/tests/test_sqlite_backup.py",
            "llmebm/main_ebm.py",
            "ebm-rag/lava/adapter/base.py",
            "ebm-rag/lava/adapter/google.py",
            "ebm-rag/lava/adapter/openai_compatible.py",
            "ebm-rag/lava/schemas.py",
            "ebm-rag/lava/task_registry.py",
            "ebm-rag/lava/api_router.py",
            "ebm-rag/lava/matching_tasks/topic_content_plan.py",
            "ebm-rag/lava/matching_tasks/topic_content_compose.py",
            "ebm-rag/rag_core/common/state_db.py",
            "ebm-rag/rag_core/common/evidence_scope.py",
            "ebm-rag/rag_core/core1_ingestion/metadata.py",
            "ebm-rag/rag_core/core4_ragging/retriever.py",
            "ebm-rag/rag_core/core4_ragging/topic_content.py",
            "ebm-rag/rag_core/core5_api/schemas.py",
            "ebm-rag/rag_core/core5_api/router.py",
            "ebm-rag/docker-compose-rag.yml",
            "ebm-rag/tools/prepare_af_topic_corpus.py",
            "ebm-rag/tests/test_topic_content_contract.py",
        )
        required_fields = (
            "模組定位", "主要責任", "呼叫來源", "輸入契約",
            "輸出契約", "安全邊界", "維護提醒",
        )
        for relative_path in paths:
            with self.subTest(path=relative_path):
                header = (repo_root / relative_path).read_text(encoding="utf-8")[:2500]
                for field in required_fields:
                    self.assertIn(field, header)


class GenerationAuthorizationTests(unittest.TestCase):
    def test_compose_loads_review_token_from_topic_secret_file(self):
        compose = (LLMEBM_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("- ../.env.topic", compose)
        self.assertNotIn("LLMEBM_REVIEW_ADMIN_TOKEN=", compose)

    def test_default_policy_rejects_remote_and_allows_loopback(self):
        self.assertFalse(generation_request_authorized("203.0.113.10", None, configured_token=""))
        self.assertTrue(generation_request_authorized("127.0.0.1", None, configured_token=""))

    def test_configured_token_is_required_and_compared(self):
        self.assertFalse(generation_request_authorized("127.0.0.1", None, configured_token="secret"))
        self.assertFalse(generation_request_authorized("127.0.0.1", "wrong", configured_token="secret"))
        self.assertTrue(generation_request_authorized("203.0.113.10", "secret", configured_token="secret"))

    def test_development_review_session_requires_nonproduction_environment_and_token(self):
        self.assertTrue(development_review_session_enabled("local", "secret", "localhost"))
        self.assertTrue(development_review_session_enabled("development", "secret", "127.0.0.1"))
        self.assertFalse(development_review_session_enabled("production", "secret", "localhost"))
        self.assertFalse(development_review_session_enabled("local", "", "localhost"))
        self.assertFalse(development_review_session_enabled("local", "secret", "192.168.1.20"))


class TopicGenerationBatchContractTests(unittest.TestCase):
    def test_selection_is_status_priority_then_manifest_order(self):
        manifest = {"slots": [
            {"slot_id": "ready"}, {"slot_id": "empty-1"}, {"slot_id": "stale-1"},
            {"slot_id": "failed-1"}, {"slot_id": "stale-2"}, {"slot_id": "empty-2"},
        ]}
        status_payload = {"slots": {
            "ready": {"status": "ready"}, "empty-1": {"status": "empty"},
            "stale-1": {"status": "stale"}, "failed-1": {"status": "failed"},
            "stale-2": {"status": "stale"}, "empty-2": {"status": "empty"},
        }}
        self.assertEqual(
            select_generation_candidates(manifest, status_payload, ("stale", "failed", "empty")),
            ["stale-1", "stale-2", "failed-1", "empty-1", "empty-2"],
        )

    def test_default_batches_are_small_and_lossless(self):
        slot_ids = [f"slot-{index}" for index in range(11)]
        self.assertEqual(build_batches(slot_ids, 5), [slot_ids[:5], slot_ids[5:10], slot_ids[10:]])
        with self.assertRaisesRegex(ValueError, "batch_size"):
            build_batches(slot_ids, 0)

    def test_explicit_scope_cannot_add_an_ineligible_slot(self):
        self.assertEqual(restrict_candidates(["a", "b", "c"], ["c", "a"]), ["a", "c"])
        with self.assertRaisesRegex(ValueError, "not currently eligible"):
            restrict_candidates(["a", "b"], ["a", "ready-slot"])

    def test_generation_submission_requires_current_scope_review(self):
        server = (LLMEBM_ROOT / "main_ebm.py").read_text(encoding="utf-8")
        runner = (LLMEBM_ROOT / "tools" / "generate_topic_content_batches.py").read_text(encoding="utf-8")
        self.assertIn("require_current_scope_review: bool = True", server)
        self.assertIn("payload.require_current_scope_review", server)
        self.assertIn('get("current_approved")', server)
        self.assertIn('"require_current_scope_review": True', runner)


if __name__ == "__main__":
    unittest.main()
