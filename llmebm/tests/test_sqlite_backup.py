"""
模組定位: llmebm SQLite backup/restore 工具的最小資料安全回歸。
主要責任: 驗證一致快照、digest gate、拒絕無授權覆寫與 pre-restore safety backup。
呼叫來源: unittest discovery 與 release/DR gate。
輸入契約: TemporaryDirectory 中的 SQLite fixtures，不讀 production DB。
輸出契約: 所有測試通過，且還原後資料與備份內容一致。
安全邊界: 不接觸 live container、外部網路、secret 或 PHI。
維護提醒: 修改 restore 覆寫策略時必須同步 runbook 與失敗案例。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from llmebm.tools.sqlite_backup import backup_database, restore_database, verify_database


class SQLiteBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.db"
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO records(value) VALUES ('before')")
            connection.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def value(path: Path) -> str:
        with closing(sqlite3.connect(path)) as connection:
            return connection.execute("SELECT value FROM records WHERE id=1").fetchone()[0]

    def test_backup_is_consistent_and_refuses_overwrite(self):
        backup = self.root / "backup.db"
        receipt = backup_database(self.source, backup)
        self.assertEqual(receipt["integrity"], "ok")
        self.assertEqual(receipt["sha256"], verify_database(backup)["sha256"])
        self.assertEqual(self.value(backup), "before")
        with self.assertRaisesRegex(ValueError, "already exists"):
            backup_database(self.source, backup)

    def test_restore_requires_digest_and_replace_then_preserves_old_target(self):
        backup = self.root / "backup.db"
        digest = backup_database(self.source, backup)["sha256"]
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("UPDATE records SET value='after' WHERE id=1")
            connection.commit()

        with self.assertRaisesRegex(ValueError, "expected-sha256"):
            restore_database(backup, self.source, expected_sha256="sha256:" + "0" * 64, replace=True)
        with self.assertRaisesRegex(ValueError, "Target exists"):
            restore_database(backup, self.source, expected_sha256=digest)

        receipt = restore_database(backup, self.source, expected_sha256=digest, replace=True)
        self.assertEqual(self.value(self.source), "before")
        safety_backup = Path(receipt["pre_restore_backup"]["destination"])
        self.assertEqual(self.value(safety_backup), "after")
        self.assertEqual(verify_database(self.source)["integrity"], "ok")


if __name__ == "__main__":
    unittest.main()
