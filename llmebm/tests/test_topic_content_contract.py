# File path: rootmedicals-a/llmebm/tests/test_topic_content_contract.py
# Description: Small stdlib contract checks for dynamic Topic Page infrastructure.
# ----------------------------------------------------------------------------------------------------

import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

IMPORT_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["LLMEBM_TOPIC_CONTENT_DB_PATH"] = str(Path(IMPORT_TEMP_DIR.name) / "import-topic.db")
os.environ["LLMEBM_TOPIC_SCAN_ROOT"] = str(Path(IMPORT_TEMP_DIR.name) / "scans")

from app.model.sidebar_model import SidebarDatabase
from app.model.topic_content_model import (
    TopicContentDatabase,
    build_manifest,
    load_screenshot_payloads,
    manifest_hash,
    validate_content,
)
from tools.scan_topic_manifest import canonicalize_dom_slots, should_trigger_generation, validate_scan_url
from app.topic_security import generation_request_authorized


class SidebarSlotContractTests(unittest.TestCase):
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

    def test_required_topic_api_routes_exist(self):
        source = (LLMEBM_ROOT / "main_ebm.py").read_text(encoding="utf-8")
        for suffix in ("/manifest", "/content/{slot_id}", "/content/generate", "/content/status"):
            self.assertIn(f'/api/v1/topic/{{topic_name}}{suffix}', source)


class GenerationAuthorizationTests(unittest.TestCase):
    def test_default_policy_rejects_remote_and_allows_loopback(self):
        self.assertFalse(generation_request_authorized("203.0.113.10", None, configured_token=""))
        self.assertTrue(generation_request_authorized("127.0.0.1", None, configured_token=""))

    def test_configured_token_is_required_and_compared(self):
        self.assertFalse(generation_request_authorized("127.0.0.1", None, configured_token="secret"))
        self.assertFalse(generation_request_authorized("127.0.0.1", "wrong", configured_token="secret"))
        self.assertTrue(generation_request_authorized("203.0.113.10", "secret", configured_token="secret"))


if __name__ == "__main__":
    unittest.main()
