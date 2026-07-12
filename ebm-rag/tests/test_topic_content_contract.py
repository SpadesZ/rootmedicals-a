import asyncio
import base64
import pathlib
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

EBM_RAG_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from lava.adapter.base import BaseLavaAdapter
from lava.adapter.google import GoogleAdapter
from lava.adapter.openai_compatible import OpenAICompatibleAdapter
from lava.api_router import _task_readiness
from lava.matching_tasks.topic_content_compose import (
    execute_topic_content_compose,
    validate_topic_content,
)
from lava.matching_tasks.topic_content_plan import execute_topic_content_plan, validate_topic_plan
from lava.task_registry import RAG_TASKS
from rag_core.core4_ragging.topic_content import generate_topic_content
from rag_core.core5_api.schemas import TopicContentGenerateRequest
from rag_core.core5_api.router import _authorize_topic_content_request, router
from fastapi import HTTPException


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def manifest(slot_count=1):
    slots = []
    for index in range(slot_count):
        slots.append({
            "slot_id": f"AAA-UID:universal:u{index + 1}",
            "heading": f"Heading {index + 1}",
            "heading_path": ["Diagnosis", f"Heading {index + 1}"],
            "level": 2,
            "order": index,
            "content_target": True,
            "allowed_blocks": ["summary", "bullets", "table"],
        })
    return {
        "schema": "llmebm-topic-manifest.v1",
        "topic_uid": "AAA-UID",
        "topic_name": "Abdominal Aortic Aneurysm",
        "template_version": "condition.v1",
        "dom_hash": "sha256:" + "a" * 64,
        "slots": slots,
    }


def plan_for(source_manifest):
    return {
        "schema": "llmebm-topic-plan.v1",
        "topic_uid": source_manifest["topic_uid"],
        "manifest_hash": source_manifest["dom_hash"],
        "sections": [{
            "slot_id": slot["slot_id"],
            "evidence_needs": ["diagnostic indications"],
            "query_intents": ["guideline", "diagnosis"],
            "filters": {"disease": source_manifest["topic_name"]},
            "top_k": 10,
            "block_types": ["summary"],
        } for slot in source_manifest["slots"]],
    }


def evidence_bundle(slot_id, hits=True):
    return {
        "schema": "llmebm-evidence-bundle.v1",
        "slot_id": slot_id,
        "query_id": "query-1",
        "queries": ["AAA diagnostic guideline"],
        "phases": ["phase1_strict_preferred"],
        "coverage": {"status": "candidate_evidence" if hits else "insufficient", "missing_needs": []},
        "hits": [{
            "paper_id": "paper-1",
            "chunk_id": "chunk-1",
            "title": "Guideline",
            "source_type": "guideline",
            "six_s_level": "Summaries",
            "ocebm_level": "Level_1",
            "is_guideline": True,
            "score": 0.9,
            "text": "Evidence text.",
        }] if hits else [],
    }


class VisionContractTests(unittest.TestCase):
    def test_image_bounds_and_signature(self):
        normalized = BaseLavaAdapter.validate_vision_images([
            {"mime_type": "image/png", "image_b64": base64.b64encode(PNG_BYTES).decode("ascii")}
        ])
        self.assertEqual(normalized[0]["data"], PNG_BYTES)
        with self.assertRaisesRegex(ValueError, "valid PNG"):
            BaseLavaAdapter.validate_vision_images([
                {"mime_type": "image/png", "image_b64": base64.b64encode(b"not-png").decode("ascii")}
            ])
        with self.assertRaisesRegex(ValueError, "1 to 3"):
            BaseLavaAdapter.validate_vision_images([])

    def test_core5_request_rejects_extra_fields(self):
        payload = {
            "manifest": manifest(),
            "screenshots": [{
                "mime_type": "image/png",
                "image_b64": base64.b64encode(PNG_BYTES).decode("ascii"),
                "unexpected": "value",
            }],
        }
        with self.assertRaises(Exception):
            TopicContentGenerateRequest.model_validate(payload)

    def test_openai_vision_maps_image_without_changing_chat_contract(self):
        adapter = OpenAICompatibleAdapter("test", "https://example.invalid/v1")
        adapter.chat = AsyncMock(return_value={"content": "ok", "model": "m", "provider": "test"})
        result = asyncio.run(adapter.vision("secret", "m", "prompt", [{"mime_type": "image/png", "data": PNG_BYTES}]))
        self.assertEqual(result["content"], "ok")
        messages = adapter.chat.await_args.args[2]
        self.assertEqual(messages[0]["content"][1]["type"], "image_url")

    def test_google_vision_maps_inline_data(self):
        class FakeResponse:
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        adapter = GoogleAdapter()
        adapter.request_with_retries = AsyncMock(return_value=FakeResponse())
        with patch("lava.adapter.google.httpx.AsyncClient", return_value=FakeClient()):
            result = asyncio.run(adapter.vision(
                "secret", "gemini", "prompt", [{"mime_type": "image/png", "data": PNG_BYTES}]
            ))
        self.assertEqual(result["content"], "ok")


class PlanAndComposeContractTests(unittest.TestCase):
    def test_planner_rejects_invalid_json(self):
        class FakeVisionAdapter:
            supports_vision = True

            def validate_vision_images(self, images):
                return BaseLavaAdapter.validate_vision_images(images)

            async def vision(self, *args, **kwargs):
                return {"content": "not json"}

            def safe_error(self, error, api_key=""):
                return str(error)

        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_plan.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch("lava.matching_tasks.topic_content_plan.get_adapter", return_value=FakeVisionAdapter()):
            result = asyncio.run(execute_topic_content_plan({
                "manifest": manifest(),
                "screenshots": [{"mime_type": "image/png", "data": PNG_BYTES}],
            }))
        self.assertEqual(result["status"], "failed")
        self.assertIn("Invalid topic plan", result["error"])

    def test_composer_rejects_invalid_json(self):
        class FakeChatAdapter:
            supports_chat = True

            async def chat(self, *args, **kwargs):
                return {"content": "not json"}

            def safe_error(self, error, api_key=""):
                return str(error)

        source = manifest()
        section = plan_for(source)["sections"][0]
        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch("lava.matching_tasks.topic_content_compose.get_adapter", return_value=FakeChatAdapter()):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": evidence_bundle(section["slot_id"]),
            }))
        self.assertEqual(result["status"], "failed")
        self.assertIn("Invalid topic content", result["error"])

    def test_plan_accepts_exact_contract(self):
        source = manifest()
        validated = validate_topic_plan(source, plan_for(source))
        self.assertEqual(validated["sections"][0]["slot_id"], source["slots"][0]["slot_id"])

    def test_plan_rejects_unknown_slot_and_unknown_block(self):
        source = manifest()
        bad_slot = plan_for(source)
        bad_slot["sections"][0]["slot_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_topic_plan(source, bad_slot)
        bad_block = plan_for(source)
        bad_block["sections"][0]["block_types"] = ["raw_html"]
        with self.assertRaisesRegex(ValueError, "disallowed"):
            validate_topic_plan(source, bad_block)

    def test_plan_rejects_url_or_html_in_llm_output(self):
        source = manifest()
        bad = plan_for(source)
        bad["sections"][0]["evidence_needs"] = ["read https://example.test"]
        with self.assertRaisesRegex(ValueError, "URL"):
            validate_topic_plan(source, bad)

    def test_content_source_gate_rejects_unknown_citation(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        content = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": section["slot_id"],
            "status": "ready",
            "blocks": [{
                "type": "summary",
                "text": "Evidence-backed statement.",
                "citations": [{"paper_id": "paper-1", "chunk_id": "wrong"}],
            }],
            "missing_evidence": [],
        }
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_topic_content(section, evidence_bundle(section["slot_id"]), content)

    def test_no_hits_returns_insufficient_without_calling_llm(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        bundle = evidence_bundle(section["slot_id"], hits=False)
        bundle["coverage"]["missing_needs"] = section["evidence_needs"]
        result = asyncio.run(execute_topic_content_compose({"section": section, "evidence_bundle": bundle}))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["content"]["blocks"], [])


class OrchestrationAndReadinessTests(unittest.TestCase):
    def test_existing_and_topic_routes_are_registered(self):
        paths = {route.path for route in router.routes}
        self.assertIn("/api/v1/rag/query", paths)
        self.assertIn("/api/v1/rag/check", paths)
        self.assertIn("/api/v1/rag/topic-content/generate", paths)
        self.assertIn("/api/v1/rag/topic-content/readiness", paths)

    def test_topic_generate_guard_rejects_remote_without_token(self):
        request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"), headers={})
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(HTTPException) as raised:
            _authorize_topic_content_request(request)
        self.assertEqual(raised.exception.status_code, 403)

    def test_topic_generate_guard_accepts_configured_token(self):
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.8"),
            headers={"X-LLMEBM-Topic-Token": "shared-secret"},
        )
        with patch.dict("os.environ", {"LLMEBM_TOPIC_GENERATION_TOKEN": "shared-secret"}, clear=True):
            _authorize_topic_content_request(request)

    def test_optional_topic_tasks_do_not_downgrade_global_readiness(self):
        by_id = {item["task_id"]: item for item in RAG_TASKS}
        self.assertFalse(by_id["topic_content_plan"]["required"])
        self.assertFalse(by_id["topic_content_compose"]["required"])
        required = [item for item in RAG_TASKS if item["required"]]
        bindings = [{"task_id": item["task_id"], "connection_id": index + 1} for index, item in enumerate(required)]
        fake_connection = {
            "has_key": True,
            "model_id": "model",
            "is_active": 1,
            "verify_status": "ok",
            "verified_capability": "chat",
            "provider": "openai",
        }

        def connection_for(connection_id, include_secret=False):
            if not connection_id:
                return None
            result = dict(fake_connection)
            task = required[connection_id - 1]
            result["verified_capability"] = task["capability"]
            return result

        with patch("lava.api_router.LLMModel.list_bindings", return_value=bindings), \
             patch("lava.api_router.LLMModel.get_connection", side_effect=connection_for), \
             patch("lava.api_router._module_implemented", return_value=True):
            readiness = _task_readiness()
        self.assertTrue(readiness["ready"])
        self.assertFalse(readiness["tasks"]["topic_content_plan"]["ready"])

    def test_single_slot_failure_keeps_other_slot_result(self):
        source = manifest(slot_count=2)
        planned = plan_for(source)
        retrieval = {
            "status": "ok",
            "queries": ["AAA guideline diagnosis"],
            "phases": [{"name": "phase1_strict_preferred"}],
            "hits": [{
                "paper_id": "paper-1",
                "chunk_id": "chunk-1",
                "text": "Evidence",
                "score": 0.9,
                "six_s_level": "Summaries",
                "ocebm_level": "Level_1",
                "section_title": "Diagnosis",
                "payload": {"source_type": "guideline", "is_guideline": True},
            }],
        }
        good_content = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": source["slots"][0]["slot_id"],
            "status": "ready",
            "blocks": [],
            "missing_evidence": [],
            "model": {"provider": "test", "model_id": "test"},
        }
        compose_results = [
            {"status": "ready", "content": good_content, "error": None},
            {"status": "failed", "content": None, "error": "bad compose"},
        ]
        with patch(
            "rag_core.core4_ragging.topic_content.execute_topic_content_plan",
            AsyncMock(return_value={"status": "ok", "plan": planned, "error": None}),
        ), patch(
            "rag_core.core4_ragging.topic_content.retrieve", AsyncMock(return_value=retrieval)
        ), patch(
            "rag_core.core4_ragging.topic_content.execute_topic_content_compose",
            AsyncMock(side_effect=compose_results),
        ), patch(
            "rag_core.core4_ragging.topic_content.sdb.save_retrieval_log", AsyncMock()
        ):
            result = asyncio.run(generate_topic_content(
                source,
                [{"mime_type": "image/png", "data": PNG_BYTES}],
            ))
        self.assertEqual(result["status"], "partial")
        self.assertIsNotNone(result["sections"][0]["content"])
        self.assertIsNone(result["sections"][1]["content"])
        self.assertRegex(result["sections"][0]["evidence_digest"], r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
