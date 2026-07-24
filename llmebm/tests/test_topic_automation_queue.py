# 模組定位: llmebm 結構掃描與逐 slot regeneration durable queue 契約測試。
# 主要責任: 驗證精準 affected-slot、active-job 去重、原子 claim、重啟恢復與 mapping wait/resume。
# 呼叫來源: unittest discovery 與核心閉環施工驗收。
# 輸入契約: 暫存 SQLite、llmebm-topic-manifest.v1 與受控 fake scanner/generation client。
# 輸出契約: 任何 queue 重複執行、漏單、跨 slot 污染或錯誤恢復都必須使測試失敗。
# 安全邊界: 不連外、不呼叫真 LLM、不修改 live topic_content.db。
# 維護提醒: automation 狀態機或 retry 規則變更時，先更新這份契約再改 worker。
# ----------------------------------------------------------------------------------------------------

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.model.topic_content_model import (
    TopicContentDatabase,
    affected_manifest_slot_ids,
    build_manifest,
)
from tools.run_topic_automation_worker import (
    AutomationHttpError,
    process_automation_job,
    reconcile_evidence_staleness,
)


class TopicAutomationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = TopicContentDatabase(str(Path(self.temp_dir.name) / "topic.db"))
        self.before = build_manifest(
            "atrial-fibrillation",
            "topic-af",
            {
                "universal": [
                    {
                        "slot_id": "topic-af:universal:overview",
                        "name": "Overview",
                        "layer": 1,
                        "content_target": True,
                        "allowed_blocks": ["summary"],
                        "children": [],
                    },
                    {
                        "slot_id": "topic-af:universal:diagnosis",
                        "name": "Diagnosis",
                        "layer": 1,
                        "content_target": True,
                        "allowed_blocks": ["summary"],
                        "children": [],
                    },
                ],
            },
        )
        self.after = build_manifest(
            "atrial-fibrillation",
            "topic-af",
            {
                "universal": [
                    {
                        "slot_id": "topic-af:universal:overview",
                        "name": "Overview and Recommendations",
                        "layer": 1,
                        "content_target": True,
                        "allowed_blocks": ["summary"],
                        "children": [],
                    },
                    {
                        "slot_id": "topic-af:universal:diagnosis",
                        "name": "Diagnosis",
                        "layer": 1,
                        "content_target": True,
                        "allowed_blocks": ["summary"],
                        "children": [],
                    },
                    {
                        "slot_id": "topic-af:universal:management",
                        "name": "Management",
                        "layer": 1,
                        "content_target": True,
                        "allowed_blocks": ["recommendations"],
                        "children": [],
                    },
                ],
            },
        )
        self.db.save_manifest(self.before)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_affected_slots_exclude_unchanged_and_removed_nodes(self):
        self.assertEqual(
            affected_manifest_slot_ids(self.before, self.after),
            [
                "topic-af:universal:overview",
                "topic-af:universal:management",
            ],
        )

    def test_save_and_enqueue_is_deduplicated_while_active(self):
        affected = affected_manifest_slot_ids(self.before, self.after)
        stored, first_job_id = self.db.save_manifest_and_enqueue_scan(self.after, affected)
        second_job_id = self.db.enqueue_automation_job(
            topic_uid=stored["topic_uid"],
            topic_name=stored["topic_name"],
            job_type="scan",
            dedupe_key=f"scan:{stored['topic_uid']}:{stored['dom_hash']}",
            payload={"expected_manifest_hash": stored["dom_hash"], "affected_slot_ids": affected},
        )

        self.assertEqual(first_job_id, second_job_id)
        self.assertEqual(self.db.automation_status(stored["topic_uid"])["counts"], {"pending": 1})

    def test_claim_is_atomic_and_restart_recovery_requeues_running_job(self):
        affected = affected_manifest_slot_ids(self.before, self.after)
        _stored, job_id = self.db.save_manifest_and_enqueue_scan(self.after, affected)

        claimed = self.db.claim_automation_job("worker-a")
        self.assertEqual(claimed["job_id"], job_id)
        self.assertIsNone(self.db.claim_automation_job("worker-b"))

        self.assertEqual(self.db.recover_running_automation_jobs(), 1)
        reclaimed = self.db.claim_automation_job("worker-b")
        self.assertEqual(reclaimed["job_id"], job_id)
        self.assertEqual(reclaimed["attempt_count"], 2)

    def test_scan_job_enqueues_only_affected_slot_regeneration(self):
        affected = affected_manifest_slot_ids(self.before, self.after)
        stored, _job_id = self.db.save_manifest_and_enqueue_scan(self.after, affected)
        claimed = self.db.claim_automation_job("worker-a")

        def fake_scan(**kwargs):
            self.assertFalse(kwargs["generate"])
            return {"status": "visual_updated", "manifest": stored, "generation": None}

        process_automation_job(self.db, claimed, scan_func=fake_scan)
        status = self.db.automation_status(stored["topic_uid"])
        queued = [job for job in status["jobs"] if job["job_type"] == "regenerate"]

        self.assertEqual(status["counts"], {"completed": 1, "pending": 2})
        self.assertEqual(
            {job["payload"]["slot_id"] for job in queued},
            set(affected),
        )

    def test_mapping_gate_waits_and_approval_resumes_same_job(self):
        slot_id = "topic-af:universal:overview"
        job_id = self.db.enqueue_automation_job(
            topic_uid="topic-af",
            topic_name="atrial-fibrillation",
            job_type="regenerate",
            dedupe_key=f"regenerate:topic-af:{self.before['dom_hash']}:{slot_id}",
            payload={"manifest_hash": self.before["dom_hash"], "slot_id": slot_id},
        )
        claimed = self.db.claim_automation_job("worker-a")

        def mapping_blocked(_topic_name, _slot_id):
            raise AutomationHttpError(409, "Current evidence mapping review is required")

        process_automation_job(self.db, claimed, generation_request=mapping_blocked)
        waiting = self.db.get_automation_job(job_id)
        self.assertEqual(waiting["status"], "waiting_review")

        self.assertEqual(self.db.resume_waiting_regeneration("topic-af", slot_id), 1)
        resumed = self.db.claim_automation_job("worker-b")
        self.assertEqual(resumed["job_id"], job_id)

    def test_accepted_generation_is_followed_to_terminal_state(self):
        slot_id = "topic-af:universal:overview"
        job_id = self.db.enqueue_automation_job(
            topic_uid="topic-af",
            topic_name="atrial-fibrillation",
            job_type="regenerate",
            dedupe_key=f"regenerate:topic-af:{self.before['dom_hash']}:{slot_id}",
            payload={"manifest_hash": self.before["dom_hash"], "slot_id": slot_id},
        )
        claimed = self.db.claim_automation_job("worker-a")

        process_automation_job(
            self.db,
            claimed,
            generation_request=lambda _topic, _slot: {"status": "accepted", "job_id": "generation-1"},
            generation_wait=lambda generation_job_id: {
                "job_id": generation_job_id,
                "status": "completed",
            },
        )

        completed = self.db.get_automation_job(job_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["generation_job_id"], "generation-1")

    def test_transient_failure_retries_then_fails_at_persisted_ceiling(self):
        slot_id = "topic-af:universal:overview"
        job_id = self.db.enqueue_automation_job(
            topic_uid="topic-af",
            topic_name="atrial-fibrillation",
            job_type="regenerate",
            dedupe_key=f"regenerate:topic-af:{self.before['dom_hash']}:{slot_id}",
            payload={"manifest_hash": self.before["dom_hash"], "slot_id": slot_id},
            max_attempts=2,
        )

        def unavailable(_topic_name, _slot_id):
            raise AutomationHttpError(503, "temporary outage")

        for expected in ("pending", "failed"):
            claimed = self.db.claim_automation_job("worker-a")
            process_automation_job(
                self.db,
                claimed,
                generation_request=unavailable,
                retry_delay_seconds=0,
            )
            self.assertEqual(self.db.get_automation_job(job_id)["status"], expected)

    def test_scanner_process_start_does_not_interrupt_api_generation(self):
        generation_job_id = self.db.create_job("topic-af", self.before["dom_hash"], ["slot-1"])
        self.db.update_job(generation_job_id, "running")

        with patch.dict(os.environ, {"LLMEBM_INTERRUPT_GENERATION_JOBS_ON_START": "0"}):
            scanner_connection = TopicContentDatabase(self.db.db_path)

        self.assertEqual(scanner_connection.get_generation_job(generation_job_id)["status"], "running")

    def test_evidence_reconcile_queues_only_evidence_stale_slots_and_deduplicates(self):
        overview = "topic-af:universal:overview"
        diagnosis = "topic-af:universal:diagnosis"
        revision = "sha256:" + "e" * 64

        def fake_status(_topic_name):
            return {
                "slots": {
                    overview: {"status": "stale", "stale_reasons": ["evidence"]},
                    diagnosis: {"status": "stale", "stale_reasons": ["structure"]},
                },
                "evidence_revisions": {overview: revision, diagnosis: revision},
            }

        first = reconcile_evidence_staleness(self.db, status_request=fake_status)
        second = reconcile_evidence_staleness(self.db, status_request=fake_status)
        queued = self.db.automation_status("topic-af")["jobs"]

        self.assertEqual(first, second)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["slot_id"], overview)
        self.assertEqual(queued[0]["payload"]["trigger"], "evidence_revision_change")

        self.db.update_automation_job(queued[0]["job_id"], "completed")
        third = reconcile_evidence_staleness(self.db, status_request=fake_status)
        self.assertEqual(third, first)
        self.assertEqual(len(self.db.automation_status("topic-af")["jobs"]), 1)

    def test_changed_evidence_supersedes_old_job_before_llm(self):
        slot_id = "topic-af:universal:overview"
        old_revision = "sha256:" + "a" * 64
        new_revision = "sha256:" + "b" * 64
        job_id = self.db.enqueue_automation_job(
            topic_uid="topic-af",
            topic_name="atrial-fibrillation",
            job_type="regenerate",
            dedupe_key=f"regenerate:topic-af:{self.before['dom_hash']}:{slot_id}:evidence:{old_revision}",
            payload={
                "manifest_hash": self.before["dom_hash"],
                "slot_id": slot_id,
                "evidence_revision": old_revision,
                "trigger": "evidence_revision_change",
            },
            reuse_terminal=True,
        )
        claimed = self.db.claim_automation_job("worker-a")

        process_automation_job(
            self.db,
            claimed,
            status_request=lambda _topic: {"evidence_revisions": {slot_id: new_revision}},
            generation_request=lambda *_args: self.fail("superseded evidence must not call generation"),
        )

        job = self.db.get_automation_job(job_id)
        self.assertEqual(job["status"], "superseded")
        self.assertEqual(job["result"]["current_evidence_revision"], new_revision)


if __name__ == "__main__":
    unittest.main()
