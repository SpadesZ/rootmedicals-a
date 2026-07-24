# 模組定位: llmebm hierarchy admin transaction/integrity/audit/rollback contract suite。
# 主要責任: 驗證 rename/move/retire 的 stable identity、cycle gate、optimistic hash 與 rollback。
# 呼叫來源: 本機 unittest、Topic/Hierarchy Admin Phase gate 與 release verification。
# 輸入契約: temp sidebar SQLite 與 SQL-owned condition.v1 template；不依賴瀏覽器或外網。
# 輸出契約: deterministic pass/fail；任何非法 parent、衝突 preview 或 slot 位移都拒絕。
# 安全邊界: 測試只改 temp DB；正式操作必須再經 admin token/RBAC 與 DOM screenshot preview。
# 維護提醒: 新增 hierarchy mutation 時必須同步 preview、audit、rollback 與 stable-slot assertions。
# ----------------------------------------------------------------------------------------------------

import os
from pathlib import Path
import sys
import tempfile
import unittest


IMPORT_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["LLMEBM_SIDEBAR_DB_PATH"] = str(Path(IMPORT_TEMP_DIR.name) / "import-sidebar.db")
LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

from app.model.sidebar_model import SidebarDatabase
from app.topic_security import hierarchy_admin_request_authorized, mutation_rate_allowed


class HierarchyAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SidebarDatabase(str(Path(self.temp_dir.name) / "sidebar.db"))
        self.topic = "hierarchy-admin-test"
        self.db.get_sidebar_tree(self.topic)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rename_preserves_every_slot_id_and_rollback_restores_heading(self):
        before_slots = self.db.reference_import_snapshot(self.topic)["slot_ids"]
        preview = self.db.preview_hierarchy_change(
            self.topic, "universal", "u1", {"type": "rename", "name": "Reviewed Overview"},
        )
        applied = self.db.apply_hierarchy_change(
            self.topic, "universal", "u1", {"type": "rename", "name": "Reviewed Overview"},
            "admin-1", preview["before_hash"], "rename smoke",
        )
        self.assertEqual(self.db.reference_import_snapshot(self.topic)["slot_ids"], before_slots)
        self.assertEqual(self.db.get_sidebar_tree(self.topic)["universal"][0]["name"], "Reviewed Overview")
        self.db.rollback_hierarchy_change(applied["audit_id"], "admin-1", "rollback smoke")
        self.assertNotEqual(self.db.get_sidebar_tree(self.topic)["universal"][0]["name"], "Reviewed Overview")
        self.assertEqual(self.db.reference_import_snapshot(self.topic)["slot_ids"], before_slots)

    def test_cycle_and_stale_preview_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.db.preview_hierarchy_change(
                self.topic, "universal", "u1", {"type": "move", "parent_node_key": "u1-1"},
            )
        preview = self.db.preview_hierarchy_change(
            self.topic, "universal", "u1", {"type": "rename", "name": "First"},
        )
        second = self.db.preview_hierarchy_change(
            self.topic, "universal", "u2", {"type": "rename", "name": "Second"},
        )
        self.db.apply_hierarchy_change(
            self.topic, "universal", "u2", {"type": "rename", "name": "Second"},
            "admin-2", second["before_hash"],
        )
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            self.db.apply_hierarchy_change(
                self.topic, "universal", "u1", {"type": "rename", "name": "First"},
                "admin-1", preview["before_hash"],
            )

    def test_retire_leaf_is_soft_and_restorable(self):
        with self.db.get_connection() as conn:
            leaf = conn.execute(
                """SELECT n.node_key FROM sidebar_nodes n
                   WHERE n.topic_uid=? AND n.status='published'
                     AND NOT EXISTS (SELECT 1 FROM sidebar_nodes c WHERE c.parent_id=n.id AND c.status='published')
                   ORDER BY n.id LIMIT 1""",
                (self.db.topic_uid_for(self.topic),),
            ).fetchone()["node_key"]
        before_slots = self.db.reference_import_snapshot(self.topic)["slot_ids"]
        preview = self.db.preview_hierarchy_change(
            self.topic, "universal", leaf, {"type": "retire"},
        )
        self.db.apply_hierarchy_change(
            self.topic, "universal", leaf, {"type": "retire"}, "admin-1", preview["before_hash"],
        )
        self.assertEqual(self.db.reference_import_snapshot(self.topic)["slot_ids"], before_slots)
        restore = self.db.preview_hierarchy_change(
            self.topic, "universal", leaf, {"type": "restore"},
        )
        self.db.apply_hierarchy_change(
            self.topic, "universal", leaf, {"type": "restore"}, "admin-1", restore["before_hash"],
        )
        self.assertEqual(self.db.reference_import_snapshot(self.topic)["slot_ids"], before_slots)

    def test_hierarchy_admin_token_is_mandatory(self):
        self.assertFalse(hierarchy_admin_request_authorized("", configured_token="secret"))
        self.assertFalse(hierarchy_admin_request_authorized("secret", configured_token=""))
        self.assertTrue(hierarchy_admin_request_authorized("secret", configured_token="secret"))

    def test_local_mutation_rate_limit_has_a_bounded_window(self):
        key = f"fixture-{id(self)}"
        self.assertTrue(mutation_rate_allowed(key, now=0, limit=2, window_seconds=60))
        self.assertTrue(mutation_rate_allowed(key, now=1, limit=2, window_seconds=60))
        self.assertFalse(mutation_rate_allowed(key, now=2, limit=2, window_seconds=60))
        self.assertTrue(mutation_rate_allowed(key, now=61, limit=2, window_seconds=60))


if __name__ == "__main__":
    unittest.main()
