# 模組定位: llmebm medical review/publish/rollback lifecycle 的 SQLite contract suite。
# 主要責任: 驗證 revision gate、狀態轉換、來源授權阻擋、published 選擇與 audit history。
# 呼叫來源: 本機 unittest、Phase P3 gate 與 release verification。
# 輸入契約: temp SQLite、單一 manifest/slot 與具 citation 的安全 component fixture。
# 輸出契約: deterministic pass/fail；generated content 絕不自動成為 published。
# 安全邊界: reviewer mutation 使用獨立 token；未核准 source gate 與 stale revision 必須拒絕。
# 維護提醒: 新增 workflow transition 時先補非法轉移與 preserve-published regression。
# ----------------------------------------------------------------------------------------------------

import os
from pathlib import Path
import sys
import tempfile
import unittest


IMPORT_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["LLMEBM_TOPIC_CONTENT_DB_PATH"] = str(Path(IMPORT_TEMP_DIR.name) / "import.db")
LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

from app.model.topic_content_model import (
    TopicContentDatabase,
    build_manifest,
    manifest_structure_revisions,
)
from app.topic_security import review_request_authorized


class ReviewPublishLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = TopicContentDatabase(str(Path(self.temp_dir.name) / "topic.db"))
        self.manifest = build_manifest("atrial-fibrillation", "topic-af", {
            "universal": [{
                "slot_id": "topic-af:universal:af-bg-definitions",
                "name": "Definitions",
                "layer": 1,
                "content_target": True,
                "allowed_blocks": ["summary"],
                "children": [],
            }],
            "custom": [],
        })
        self.db.save_manifest(self.manifest)
        self.slot_id = self.manifest["slots"][0]["slot_id"]
        self.evidence_revision = "sha256:" + "e" * 64
        self.structure_revision = manifest_structure_revisions(self.manifest)[self.slot_id]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _save_ready(self, text):
        self.db.save_section_result(
            "topic-af",
            self.manifest["dom_hash"],
            {
                "slot_id": self.slot_id,
                "status": "ready",
                "query_id": f"query-{text}",
                "evidence_digest": "sha256:" + "d" * 64,
                "evidence_revision": self.evidence_revision,
                "content": {
                    "schema": "llmebm-topic-content.v1",
                    "slot_id": self.slot_id,
                    "status": "ready",
                    "blocks": [{
                        "type": "summary",
                        "text": text,
                        "citations": [{"paper_id": "paper-1", "chunk_id": "paper-1:chunk:1"}],
                    }],
                    "missing_evidence": [],
                    "model": {"provider": "fixture", "model_id": "fixture"},
                },
            },
        )
        return self.db.get_current_content("topic-af", self.slot_id)["version_id"]

    @staticmethod
    def _allowed_gate():
        return {
            "allowed": True,
            "required_use": "commercial_publication",
            "paper_ids": ["paper-1"],
            "blocked": [],
        }

    def test_generated_review_publish_and_rollback_are_audited(self):
        first_id = self._save_ready("Version one")
        self.assertIsNone(self.db.get_published_content("topic-af", self.slot_id))
        self.db.submit_for_review(
            "topic-af", self.slot_id, "reviewer-1", self.evidence_revision,
            self.structure_revision, "submit v1",
        )
        self.db.decide_review("topic-af", self.slot_id, "reviewer-1", "approve", "approved v1")
        with self.assertRaisesRegex(ValueError, "source-use gate"):
            self.db.publish_current(
                "topic-af", self.slot_id, "publisher-1", self.evidence_revision,
                self.structure_revision, {"allowed": False}, "blocked",
            )
        self.db.publish_current(
            "topic-af", self.slot_id, "publisher-1", self.evidence_revision,
            self.structure_revision, self._allowed_gate(), "publish v1",
        )
        self.assertEqual(self.db.get_published_content("topic-af", self.slot_id)["id"], first_id)

        second_id = self._save_ready("Version two")
        self.db.submit_for_review(
            "topic-af", self.slot_id, "reviewer-2", self.evidence_revision,
            self.structure_revision,
        )
        self.db.decide_review("topic-af", self.slot_id, "reviewer-2", "approve")
        self.db.publish_current(
            "topic-af", self.slot_id, "publisher-2", self.evidence_revision,
            self.structure_revision, self._allowed_gate(),
        )
        self.assertEqual(self.db.get_published_content("topic-af", self.slot_id)["id"], second_id)
        self.db.rollback_publication(
            "topic-af", self.slot_id, first_id, "publisher-1", self.evidence_revision,
            self.structure_revision, self._allowed_gate(), "rollback",
        )
        self.assertEqual(self.db.get_published_content("topic-af", self.slot_id)["id"], first_id)
        self.assertEqual(
            [item["action"] for item in self.db.get_review_history("topic-af", self.slot_id)],
            ["submit", "approve", "publish", "submit", "approve", "publish", "rollback"],
        )

    def test_stale_revision_and_missing_review_token_fail_closed(self):
        self._save_ready("Draft")
        with self.assertRaisesRegex(ValueError, "evidence revision is stale"):
            self.db.submit_for_review(
                "topic-af", self.slot_id, "reviewer", "sha256:" + "f" * 64,
                self.structure_revision,
            )
        self.assertFalse(review_request_authorized("", configured_token="secret"))
        self.assertFalse(review_request_authorized("secret", configured_token=""))
        self.assertTrue(review_request_authorized("secret", configured_token="secret"))

    def test_review_queue_lists_current_versions_and_filters_workflow_status(self):
        version_id = self._save_ready("Queue draft")
        generated = self.db.list_review_queue("topic-af")
        self.assertEqual([item["version_id"] for item in generated], [version_id])
        self.assertEqual(generated[0]["workflow_status"], "generated")
        self.assertEqual(generated[0]["paper_ids"], ["paper-1"])
        self.assertEqual(generated[0]["structure_revision"], self.structure_revision)

        self.db.submit_for_review(
            "topic-af", self.slot_id, "reviewer", self.evidence_revision,
            self.structure_revision, "queue submit",
        )
        self.assertEqual(
            self.db.list_review_queue("topic-af", {"review_pending"})[0]["workflow_status"],
            "review_pending",
        )
        self.assertEqual(self.db.list_review_queue("topic-af", {"approved"}), [])
        with self.assertRaisesRegex(ValueError, "workflow status"):
            self.db.list_review_queue("topic-af", {"invalid"})


if __name__ == "__main__":
    unittest.main()
