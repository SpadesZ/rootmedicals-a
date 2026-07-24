# 模組定位: ebm-rag Topic content/LAVA/readiness 的最小 contract suite。
# 主要責任: 驗證 vision bounds、plan/compose gates、逐 slot 容錯、授權與 evidence revision contract。
# 呼叫來源: 開發者本機 unittest 與 Phase 驗收命令。
# 輸入契約: in-memory fixtures、mock adapters/retrieval 與 deterministic signatures。
# 輸出契約: 不呼叫外部模型或網路的 repeatable pass/fail。
# 安全邊界: 臨床內容只使用 fixture evidence；所有 provider 呼叫均 mock。
# 維護提醒: section failure 必須同時保留其他成功內容並回傳結構化 stage/error。
# ----------------------------------------------------------------------------------------------------

import asyncio
import base64
import json
import pathlib
import sqlite3
import sys
import tempfile
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
from lava.matching_tasks.topic_content_plan import (
    _bind_plan_identity,
    _planner_prompt,
    execute_topic_content_plan,
    validate_topic_plan,
)
from lava.task_registry import RAG_TASKS
from rag_core.core4_ragging.retriever import _build_query_plan, _qdrant_filter
from rag_core.core4_ragging.topic_content import _section_context, generate_topic_content
from rag_core.common import state_db as sdb
from rag_core.common.evidence_scope import canonical_topic_key
from rag_core.core1_ingestion.metadata import inject_metadata
from rag_core.core5_api.schemas import (
    TopicContentGenerateRequest,
    TopicEvidenceRevisionRequest,
    TopicScopeReviewApproveRequest,
    TopicScopeReviewStatusRequest,
)
from rag_core.core5_api.router import (
    _authorize_topic_content_request,
    _combined_evidence_revision,
    _external_meta_from_body,
    router,
    topic_scope_review_approve_current_endpoint,
    topic_scope_review_status_endpoint,
    topic_content_revisions_endpoint,
)
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

    def test_google_vision_exposes_empty_max_token_finish_for_planner_fallback(self):
        class FakeResponse:
            def json(self):
                return {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}

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
        self.assertEqual(result["content"], "")
        self.assertEqual(result["finish_reason"], "MAX_TOKENS")

    def test_google_chat_retries_one_empty_stop_response(self):
        class FakeResponse:
            def __init__(self, parts):
                self.parts = parts

            def json(self):
                return {"candidates": [{"content": {"parts": self.parts}, "finishReason": "STOP"}]}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        adapter = GoogleAdapter()
        adapter.request_with_retries = AsyncMock(side_effect=[
            FakeResponse([]), FakeResponse([{"text": "ok"}]),
        ])
        with patch("lava.adapter.google.httpx.AsyncClient", return_value=FakeClient()):
            result = asyncio.run(adapter.chat("secret", "gemini", [{"role": "user", "content": "prompt"}]))
        self.assertEqual(result["content"], "ok")
        self.assertEqual(adapter.request_with_retries.await_count, 2)

    def test_google_chat_can_request_json_response_without_changing_default(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]}

        class FakeClient:
            def __init__(self):
                self.payloads = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json):
                self.payloads.append(json)
                return FakeResponse()

        client = FakeClient()
        adapter = GoogleAdapter()
        with patch("lava.adapter.google.httpx.AsyncClient", return_value=client):
            result = asyncio.run(adapter.chat(
                "secret", "gemini", [{"role": "user", "content": "prompt"}],
                response_mime_type="application/json",
            ))
        self.assertEqual("{}", result["content"])
        self.assertEqual("application/json", client.payloads[0]["generationConfig"]["responseMimeType"])


class PlanAndComposeContractTests(unittest.TestCase):
    def test_planner_prompt_declares_filter_value_types(self):
        prompt = _planner_prompt(manifest(), ["full-page"])
        payload = json.loads(prompt.split("INPUT JSON:\n", 1)[1])

        self.assertEqual("string", payload["allowed_filter_contract"]["min_ocebm_level"])
        self.assertEqual("boolean", payload["allowed_filter_contract"]["is_guideline"])
        self.assertEqual(
            "array of 1-5 strings",
            payload["allowed_filter_contract"]["prefer_six_s_levels"],
        )

    def test_provider_plan_identity_is_bound_to_trusted_manifest(self):
        source = manifest()
        raw_plan = plan_for(source)
        raw_plan["topic_uid"] = "model-transcription-error"
        raw_plan["manifest_hash"] = "sha256:" + "b" * 64

        bound = _bind_plan_identity(source, raw_plan)

        self.assertEqual(bound["topic_uid"], source["topic_uid"])
        self.assertEqual(bound["manifest_hash"], source["dom_hash"])
        self.assertEqual(validate_topic_plan(source, bound)["sections"], raw_plan["sections"])

    def test_21_slot_planner_recovers_from_token_truncation_with_bounded_batches(self):
        source = manifest(slot_count=21)

        class FakeVisionAdapter:
            supports_vision = True

            def __init__(self):
                self.calls = []

            def validate_vision_images(self, images):
                return BaseLavaAdapter.validate_vision_images(images)

            async def vision(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                if len(self.calls) == 1:
                    return {
                        "content": '{"schema":"llmebm-topic-plan.v1","sections":[',
                        "finish_reason": "MAX_TOKENS",
                    }
                prompt_payload = json.loads(args[2].split("INPUT JSON:\n", 1)[1])
                batch_manifest = prompt_payload["manifest"]
                return {"content": json.dumps(plan_for(batch_manifest)), "finish_reason": "STOP"}

            def safe_error(self, error, api_key=""):
                return str(error)

        adapter = FakeVisionAdapter()
        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_plan.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch("lava.matching_tasks.topic_content_plan.get_adapter", return_value=adapter):
            result = asyncio.run(execute_topic_content_plan({
                "manifest": source,
                "screenshots": [{"mime_type": "image/png", "data": PNG_BYTES}],
            }))

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["planning_mode"], "batched_fallback")
        self.assertEqual(
            [section["slot_id"] for section in result["plan"]["sections"]],
            [slot["slot_id"] for slot in source["slots"]],
        )
        self.assertEqual(len(adapter.calls), 5)
        self.assertEqual(adapter.calls[0][1]["max_tokens"], 8192)
        self.assertTrue(all(call[1]["max_tokens"] == 4096 for call in adapter.calls[1:]))

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
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["content"]["blocks"], [])
        self.assertIn("invalid_model_output", result["fallback_reason"])

    def test_composer_retry_includes_previous_output_and_safe_fallback(self):
        source = manifest()
        section = plan_for(source)["sections"][0]

        class CorrectingChatAdapter:
            supports_chat = True

            def __init__(self):
                self.messages = []

            async def chat(self, api_key, model_id, messages, **kwargs):
                self.messages.append(messages)
                if len(self.messages) == 1:
                    return {"content": '{"schema":"wrong"}'}
                fallback = {
                    "schema": "llmebm-topic-content.v1",
                    "slot_id": section["slot_id"],
                    "status": "insufficient_evidence",
                    "blocks": [],
                    "missing_evidence": section["evidence_needs"],
                }
                return {"content": json.dumps(fallback)}

            def safe_error(self, error, api_key=""):
                return str(error)

        adapter = CorrectingChatAdapter()
        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch("lava.matching_tasks.topic_content_compose.get_adapter", return_value=adapter):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": evidence_bundle(section["slot_id"]),
            }))

        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(adapter.messages[1][-2]["role"], "assistant")
        self.assertEqual(adapter.messages[1][-2]["content"], '{"schema":"wrong"}')

    def test_composer_accepts_single_content_envelope(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        ready = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": section["slot_id"],
            "status": "ready",
            "blocks": [{
                "type": "summary",
                "text": "Evidence text.",
                "citations": [{"paper_id": "paper-1", "chunk_id": "chunk-1"}],
            }],
            "missing_evidence": [],
        }

        class WrappedChatAdapter:
            supports_chat = True

            async def chat(self, *args, **kwargs):
                return {"content": json.dumps({"content": ready})}

            def safe_error(self, error, api_key=""):
                return str(error)

        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch(
            "lava.matching_tasks.topic_content_compose.get_adapter", return_value=WrappedChatAdapter()
        ):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": evidence_bundle(section["slot_id"]),
            }))
        self.assertEqual("ready", result["status"])

    def test_composer_accepts_content_envelope_with_provider_metadata(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        ready = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": section["slot_id"],
            "status": "ready",
            "blocks": [{
                "type": "summary",
                "text": "Evidence text.",
                "citations": [{"paper_id": "paper-1", "chunk_id": "chunk-1"}],
            }],
            "missing_evidence": [],
        }

        class MetadataWrappedChatAdapter:
            supports_chat = True

            async def chat(self, *args, **kwargs):
                return {"content": json.dumps({
                    "status": "ok",
                    "provider": "fixture",
                    "content": ready,
                })}

            def safe_error(self, error, api_key=""):
                return str(error)

        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch(
            "lava.matching_tasks.topic_content_compose.get_adapter",
            return_value=MetadataWrappedChatAdapter(),
        ):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": evidence_bundle(section["slot_id"]),
            }))
        self.assertEqual("ready", result["status"])

    def test_composer_retries_truncated_json_without_replaying_partial_output(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        ready = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": section["slot_id"],
            "status": "ready",
            "blocks": [{
                "type": "summary",
                "text": "Evidence text.",
                "citations": [{"paper_id": "paper-1", "chunk_id": "chunk-1"}],
            }],
            "missing_evidence": [],
        }

        class TruncatedThenValidChatAdapter:
            supports_chat = True

            def __init__(self):
                self.messages = []

            async def chat(self, api_key, model_id, messages, **kwargs):
                self.messages.append(messages)
                if len(self.messages) == 1:
                    return {
                        "content": '{"schema":"llmebm-topic-content.v1","blocks":[{"text":"partial',
                        "finish_reason": "MAX_TOKENS",
                    }
                return {"content": json.dumps(ready), "finish_reason": "STOP"}

            def safe_error(self, error, api_key=""):
                return str(error)

        adapter = TruncatedThenValidChatAdapter()
        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch(
            "lava.matching_tasks.topic_content_compose.get_adapter", return_value=adapter,
        ):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": evidence_bundle(section["slot_id"]),
            }))

        self.assertEqual("ready", result["status"])
        self.assertEqual(2, len(adapter.messages))
        self.assertFalse(any(message["role"] == "assistant" for message in adapter.messages[1]))
        self.assertIn("smallest valid object", adapter.messages[1][-1]["content"])

    def test_composer_reconsiders_safe_fallback_when_candidate_evidence_exists(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        fallback = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": section["slot_id"],
            "status": "insufficient_evidence",
            "blocks": [],
            "missing_evidence": section["evidence_needs"],
        }
        ready = {
            **fallback,
            "status": "ready",
            "blocks": [{
                "type": "summary",
                "text": "Evidence text.",
                "citations": [{"paper_id": "paper-1", "chunk_id": "chunk-1"}],
            }],
            "missing_evidence": [],
        }

        class FallbackThenReadyChatAdapter:
            supports_chat = True

            def __init__(self):
                self.messages = []

            async def chat(self, api_key, model_id, messages, **kwargs):
                self.messages.append(messages)
                content = fallback if len(self.messages) == 1 else ready
                return {"content": json.dumps(content), "finish_reason": "STOP"}

            def safe_error(self, error, api_key=""):
                return str(error)

        adapter = FallbackThenReadyChatAdapter()
        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch(
            "lava.matching_tasks.topic_content_compose.get_adapter", return_value=adapter,
        ):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": evidence_bundle(section["slot_id"]),
            }))

        self.assertEqual("ready", result["status"])
        self.assertEqual(2, len(adapter.messages))
        self.assertEqual("assistant", adapter.messages[1][-2]["role"])
        self.assertIn("directly supported", adapter.messages[1][-1]["content"])
        prompt_payload = json.loads(
            adapter.messages[0][0]["content"].split("INPUT JSON:\n", 1)[1]
        )
        template = prompt_payload["minimum_ready_template"]
        self.assertEqual("summary", template["blocks"][0]["type"])
        self.assertEqual(
            {"paper_id": "paper-1", "chunk_id": "chunk-1"},
            template["blocks"][0]["citations"][0],
        )

    def test_composer_bounds_prompt_evidence_hits_and_text(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        bundle = evidence_bundle(section["slot_id"])
        original = bundle["hits"][0]
        paper_ids = ["paper-rate", "paper-rate", "paper-rate", "paper-rhythm", "paper-anticoag", "paper-contra"]
        bundle["hits"] = [
            {
                **original,
                "paper_id": paper_ids[index],
                "chunk_id": f"chunk-{index}",
                "text": "Evidence sentence. " * 180,
            }
            for index in range(6)
        ]

        class PromptInspectingChatAdapter:
            supports_chat = True

            def __init__(self):
                self.prompt_payload = None

            async def chat(self, api_key, model_id, messages, **kwargs):
                self.prompt_payload = json.loads(messages[0]["content"].split("INPUT JSON:\n", 1)[1])
                ready = {
                    "schema": "llmebm-topic-content.v1",
                    "slot_id": section["slot_id"],
                    "status": "ready",
                    "blocks": [{
                        "type": "summary",
                        "text": "Evidence sentence.",
                        "citations": [{"paper_id": "paper-rate", "chunk_id": "chunk-0"}],
                    }],
                    "missing_evidence": [],
                }
                return {"content": json.dumps(ready), "finish_reason": "STOP"}

            def safe_error(self, error, api_key=""):
                return str(error)

        adapter = PromptInspectingChatAdapter()
        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch(
            "lava.matching_tasks.topic_content_compose.get_adapter", return_value=adapter,
        ):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": bundle,
            }))

        self.assertEqual("ready", result["status"])
        prompt_hits = adapter.prompt_payload["evidence_bundle"]["hits"]
        self.assertEqual(6, len(prompt_hits))
        self.assertEqual(
            ["paper-rate", "paper-rhythm", "paper-anticoag", "paper-contra"],
            list(dict.fromkeys(hit["paper_id"] for hit in prompt_hits)),
        )
        self.assertTrue(all(len(hit["text"]) <= 2000 for hit in prompt_hits))

    def test_composer_retries_broad_section_that_cites_only_one_source(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        section["evidence_needs"] = ["rate control", "rhythm control", "stroke prevention"]
        bundle = evidence_bundle(section["slot_id"])
        original = bundle["hits"][0]
        bundle["hits"] = [
            {**original, "paper_id": paper_id, "chunk_id": chunk_id, "text": text}
            for paper_id, chunk_id, text in (
                ("paper-rate", "chunk-rate", "Rate control evidence."),
                ("paper-rhythm", "chunk-rhythm", "Rhythm control evidence."),
                ("paper-anticoag", "chunk-anticoag", "Stroke prevention evidence."),
            )
        ]

        class UndercoveredThenBroadAdapter:
            supports_chat = True

            def __init__(self):
                self.messages = []

            async def chat(self, api_key, model_id, messages, **kwargs):
                self.messages.append(messages)
                citations = [{"paper_id": "paper-rate", "chunk_id": "chunk-rate"}]
                blocks = [{"type": "summary", "text": "Rate control evidence.", "citations": citations}]
                if len(self.messages) > 1:
                    blocks.append({
                        "type": "summary",
                        "text": "Stroke prevention evidence.",
                        "citations": [{"paper_id": "paper-anticoag", "chunk_id": "chunk-anticoag"}],
                    })
                return {"content": json.dumps({
                    "schema": "llmebm-topic-content.v1",
                    "slot_id": section["slot_id"],
                    "status": "ready",
                    "blocks": blocks,
                    "missing_evidence": [],
                }), "finish_reason": "STOP"}

            def safe_error(self, error, api_key=""):
                return str(error)

        adapter = UndercoveredThenBroadAdapter()
        connection = {"provider": "fake", "model_id": "model", "api_key": "secret"}
        with patch(
            "lava.matching_tasks.topic_content_compose.LLMModel.get_connection_for_task",
            return_value=connection,
        ), patch(
            "lava.matching_tasks.topic_content_compose.get_adapter", return_value=adapter,
        ):
            result = asyncio.run(execute_topic_content_compose({
                "section": section,
                "evidence_bundle": bundle,
            }))

        self.assertEqual("ready", result["status"])
        self.assertEqual(2, len(adapter.messages))
        self.assertIn("distinct supplied papers", adapter.messages[1][-1]["content"])
        cited_papers = {
            citation["paper_id"]
            for block in result["content"]["blocks"]
            for citation in block["citations"]
        }
        self.assertEqual({"paper-rate", "paper-anticoag"}, cited_papers)

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

    def test_recommendation_grade_survives_only_as_bounded_sourced_metadata(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        section["block_types"] = ["recommendations"]
        content = {
            "schema": "llmebm-topic-content.v1",
            "slot_id": section["slot_id"],
            "status": "ready",
            "blocks": [{
                "type": "recommendations",
                "text": "Evidence-backed recommendation.",
                "citations": [{"paper_id": "paper-1", "chunk_id": "chunk-1"}],
                "recommendation_strength": "Strong",
                "evidence_certainty": "Moderate certainty",
            }],
            "missing_evidence": [],
        }
        validated = validate_topic_content(section, evidence_bundle(section["slot_id"]), content)
        self.assertEqual(validated["blocks"][0]["recommendation_strength"], "Strong")
        content["blocks"][0]["recommendation_strength"] = "x" * 81
        with self.assertRaisesRegex(ValueError, "1..80"):
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
    def test_combined_evidence_revision_is_deterministic_and_change_sensitive(self):
        signatures = [{
            "provider": "test",
            "model": "embed",
            "dim": 3,
            "indexed_chunk_count": 1,
            "evidence_revision": "sha256:before",
        }, {
            "provider": "test",
            "model": "embed",
            "dim": 4,
            "indexed_chunk_count": 1,
            "evidence_revision": "sha256:second",
        }]
        first = _combined_evidence_revision(signatures)
        second = _combined_evidence_revision(list(reversed(signatures)))
        changed = _combined_evidence_revision([
            {**signatures[0], "evidence_revision": "sha256:after"}, signatures[1]
        ])
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_existing_and_topic_routes_are_registered(self):
        paths = {route.path for route in router.routes}
        self.assertIn("/api/v1/rag/query", paths)
        self.assertIn("/api/v1/rag/check", paths)
        self.assertIn("/api/v1/rag/topic-content/generate", paths)
        self.assertIn("/api/v1/rag/topic-content/readiness", paths)
        self.assertIn("/api/v1/rag/topic-content/revisions", paths)
        self.assertIn("/api/v1/rag/topic-content/scope-reviews/status", paths)
        self.assertIn("/api/v1/rag/topic-content/scope-reviews/approve-current", paths)

    def test_topic_scope_review_status_route_is_read_only_and_bounded(self):
        with self.assertRaises(Exception):
            TopicScopeReviewStatusRequest.model_validate({
                "topic_key": "atrial-fibrillation",
                "slot_ids": ["AF:universal:u1", "AF:universal:u1"],
            })
        body = TopicScopeReviewStatusRequest.model_validate({
            "topic_key": "atrial-fibrillation",
            "slot_ids": ["AF:universal:u1"],
        })
        statuses = {
            "universal:u1": {
                "slot_key": "universal:u1", "reviewed": True, "current_approved": True,
                "reason": "current",
            },
        }
        with patch(
            "rag_core.core5_api.router.sdb.get_evidence_scope_review_statuses",
            AsyncMock(return_value=statuses),
        ) as read_statuses:
            result = asyncio.run(topic_scope_review_status_endpoint(body))
        read_statuses.assert_awaited_once_with("atrial-fibrillation", ["AF:universal:u1"])
        self.assertEqual(result["schema"], "rootmedicals-scope-review-status.v1")
        self.assertEqual(result["current_approved"], 1)
        self.assertEqual(result["statuses"], statuses)

    def test_topic_scope_review_approval_uses_exact_current_sources_and_append_only_ledger(self):
        body = TopicScopeReviewApproveRequest.model_validate({
            "topic_key": "atrial-fibrillation",
            "slot_id": "AF:universal:af-mgmt-ablation-rhythm",
            "reviewed_by": "reviewer",
            "reason": "Rhythm-control source matches this ablation subsection.",
        })
        before = {
            "slot_key": "universal:af-mgmt-ablation-rhythm",
            "reviewed": False,
            "current_approved": False,
            "current_scope_revision": "sha256:" + "a" * 64,
            "current_source_ids": ["RM_AF_RHYTHM_CONTROL_2023"],
        }
        after = {**before, "reviewed": True, "current_approved": True, "reason": "current"}
        with patch("rag_core.core5_api.router._authorize_topic_content_request") as authorize, patch(
            "rag_core.core5_api.router.sdb.get_evidence_scope_review_statuses",
            AsyncMock(side_effect=[
                {"universal:af-mgmt-ablation-rhythm": before},
                {"universal:af-mgmt-ablation-rhythm": after},
            ]),
        ), patch(
            "rag_core.core5_api.router.sdb.record_evidence_scope_review_batch",
            AsyncMock(return_value={"inserted": 1}),
        ) as record:
            result = asyncio.run(topic_scope_review_approve_current_endpoint(body, SimpleNamespace()))
        authorize.assert_called_once()
        recorded = record.await_args.args[0]
        self.assertEqual(recorded["slots"][0]["source_ids"], ["RM_AF_RHYTHM_CONTROL_2023"])
        self.assertEqual(recorded["slots"][0]["slot_key"], "universal:af-mgmt-ablation-rhythm")
        self.assertEqual(result["schema"], "rootmedicals-scope-review-approval.v1")
        self.assertTrue(result["changed"])
        self.assertTrue(result["status"]["current_approved"])

    def test_topic_scope_review_approval_rejects_blank_reason_and_wildcard_slot(self):
        for slot_id, reason in (("*", "valid"), ("universal:u1", "   ")):
            with self.assertRaises(Exception):
                TopicScopeReviewApproveRequest.model_validate({
                    "topic_key": "atrial-fibrillation",
                    "slot_id": slot_id,
                    "reviewed_by": "reviewer",
                    "reason": reason,
                })

    def test_topic_revision_request_rejects_duplicate_or_unbounded_slots(self):
        with self.assertRaises(Exception):
            TopicEvidenceRevisionRequest.model_validate({
                "topic_key": "atrial-fibrillation",
                "slot_ids": ["T:universal:u1", "T:universal:u1"],
            })

    def test_topic_revision_route_uses_sqlite_signature_without_full_readiness(self):
        body = TopicEvidenceRevisionRequest.model_validate({
            "topic_key": "atrial-fibrillation",
            "slot_ids": ["AF:universal:u1"],
        })
        signature = {
            "provider": "test",
            "model": "embed",
            "dim": 3,
            "indexed_chunk_count": 1,
            "evidence_revision": "sha256:" + "a" * 64,
        }
        with patch(
            "rag_core.core5_api.router._rag_readiness",
            AsyncMock(side_effect=AssertionError("full readiness must not run")),
        ), patch(
            "rag_core.core5_api.router.LLMModel.get_connection_for_task",
            return_value={"provider": "test", "model_id": "embed"},
        ), patch(
            "rag_core.core5_api.router.sdb.get_indexed_embedding_signatures",
            AsyncMock(return_value=[signature]),
        ), patch(
            "rag_core.core5_api.router.sdb.get_topic_evidence_revisions",
            AsyncMock(return_value={"AF:universal:u1": "sha256:" + "b" * 64}),
        ):
            result = asyncio.run(topic_content_revisions_endpoint(body))
        self.assertEqual(result["evidence_revisions"]["AF:universal:u1"], "sha256:" + "b" * 64)
        self.assertRegex(result["evidence_revision"], r"^sha256:[0-9a-f]{64}$")

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
        retrieval_mock = AsyncMock(return_value=retrieval)
        with patch(
            "rag_core.core4_ragging.topic_content.execute_topic_content_plan",
            AsyncMock(return_value={"status": "ok", "plan": planned, "error": None}),
        ), patch(
            "rag_core.core4_ragging.topic_content.retrieve", retrieval_mock
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
        self.assertEqual(result["planning_mode"], "single")
        self.assertIsNotNone(result["sections"][0]["content"])
        self.assertIsNone(result["sections"][1]["content"])
        self.assertEqual(result["sections"][1]["error"], {"stage": "compose", "error": "bad compose"})
        self.assertRegex(result["sections"][0]["evidence_digest"], r"^sha256:[0-9a-f]{64}$")
        first_filters = retrieval_mock.await_args_list[0].kwargs["filters"]
        self.assertEqual("abdominal-aortic-aneurysm", first_filters["topic_key"])
        self.assertEqual("universal:u1", first_filters["slot_key"])


class QueryPlanFallbackTests(unittest.TestCase):
    def test_qdrant_filter_keeps_reviewed_topic_and_slot_scope(self):
        query_filter = _qdrant_filter(filters={
            "topic_key": "Atrial Fibrillation",
            "slot_key": "AF-UID:universal:af-bg-risk-thyroid",
        })
        conditions = {
            condition.key: getattr(condition.match, "value", None)
            for condition in query_filter.must
            if getattr(condition.match, "value", None) is not None
        }
        self.assertEqual("atrial-fibrillation", conditions["topic_key"])
        self.assertEqual("universal:af-bg-risk-thyroid", conditions["slot_keys"])
        self.assertEqual("current", conditions["source_lifecycle_status"])

    def test_topic_evidence_needs_are_preferred_as_retrieval_queries(self):
        source = manifest()
        section = plan_for(source)["sections"][0]
        section["evidence_needs"] = ["AAA ultrasound surveillance", "AAA repair threshold"]
        dx_summary, case_context = _section_context(source, section)

        result = asyncio.run(_build_query_plan(dx_summary, case_context, {}))

        self.assertEqual(case_context["retrieval_queries"], section["evidence_needs"])
        self.assertEqual(result["base_queries"][:2], section["evidence_needs"])
        self.assertIn("Abdominal Aortic Aneurysm guideline diagnosis criteria", result["base_queries"])

    def test_llm_query_strategy_failure_falls_back_to_deterministic_queries(self):
        with patch(
            "lava.matching_tasks.rag_query_strategy.execute_rag_query_strategy",
            AsyncMock(side_effect=RuntimeError("fixture expansion failure")),
        ):
            result = asyncio.run(_build_query_plan(
                "atrial fibrillation",
                {"tx": "anticoagulation"},
                {"query_decomposition_mode": "llm_assisted"},
            ))
        self.assertEqual(result["source"], "deterministic")
        self.assertEqual(result["queries"], result["base_queries"])
        self.assertEqual(result["llm_status"], "failed")
        self.assertEqual(result["fallback_reason"], "fixture expansion failure")


class EvidenceRevisionStoreTests(unittest.TestCase):
    @staticmethod
    def _chunk(chunk_id, paper_id, topic_key, slot_keys):
        return {
            "chunk_id": chunk_id,
            "paper_id": paper_id,
            "chunk_index": 0,
            "text": "Evidence text",
            "token_count": 2,
            "payload": {
                "embedding_provider": "test",
                "embedding_model": "embed",
                "embedding_dim": 3,
                "topic_key": topic_key,
                "slot_keys": slot_keys,
            },
            "indexed_status": "indexed",
        }

    def test_scoped_revision_changes_only_the_affected_topic_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "af-diagnosis", "paper-af-dx", "atrial-fibrillation", ["universal:u1"]
                )))
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "af-management", "paper-af-tx", "atrial-fibrillation", ["universal:u2"]
                )))
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "aaa-diagnosis", "paper-aaa", "abdominal-aortic-aneurysm", ["universal:u1"]
                )))
                slot_ids = ["AF-UID:universal:u1", "AF-UID:universal:u2"]
                before = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "UPDATE chunks SET text = 'Changed evidence text', evidence_updated_at = evidence_updated_at + 10 "
                        "WHERE chunk_id = 'af-diagnosis'"
                    )
                    conn.commit()
                after = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
            finally:
                sdb.RAG_DB_PATH = original_path

            self.assertNotEqual(before[slot_ids[0]], after[slot_ids[0]])
            self.assertEqual(before[slot_ids[1]], after[slot_ids[1]])

    def test_source_policy_change_updates_only_slots_using_that_paper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            slot_ids = ["AF-UID:universal:u1", "AF-UID:universal:u2"]
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                asyncio.run(sdb.upsert_paper("paper-1", "p1.pdf", "p1.pdf"))
                asyncio.run(sdb.upsert_paper("paper-2", "p2.pdf", "p2.pdf"))
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "chunk-1", "paper-1", "atrial-fibrillation", ["universal:u1"]
                )))
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "chunk-2", "paper-2", "atrial-fibrillation", ["universal:u2"]
                )))
                before = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
                asyncio.run(sdb.sync_paper_source_policy("paper-1", {
                    "document_version": "v2",
                    "lifecycle_status": "withdrawn",
                    "license_status": "approved",
                }))
                after = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
            finally:
                sdb.RAG_DB_PATH = original_path
            self.assertNotEqual(before[slot_ids[0]], after[slot_ids[0]])
            self.assertEqual(before[slot_ids[1]], after[slot_ids[1]])

    def test_scope_review_is_append_only_idempotent_and_current_scope_bound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            review = {
                "review_batch_id": "demo-review-1",
                "mapping_version": "demo.v1",
                "topic_key": "atrial-fibrillation",
                "reviewed_by": "reviewer-fixture",
                "reviewed_at": "2026-07-16",
                "review_hash": "sha256:fixture",
                "slots": [{
                    "slot_key": "universal:u1",
                    "decision": "approved_demo",
                    "reason": "Fixture source is appropriate for this slot.",
                    "source_ids": ["paper-1"],
                }],
            }
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                asyncio.run(sdb.upsert_paper("paper-1", "p1.pdf", "p1.pdf"))
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "chunk-1", "paper-1", "atrial-fibrillation", ["universal:u1"]
                )))
                first = asyncio.run(sdb.record_evidence_scope_review_batch(review))
                second = asyncio.run(sdb.record_evidence_scope_review_batch(review))
                current = asyncio.run(sdb.get_evidence_scope_review_statuses(
                    "atrial-fibrillation", ["universal:u1"]
                ))["universal:u1"]
                self.assertEqual(1, first["inserted"])
                self.assertEqual(0, second["inserted"])
                self.assertTrue(current["current_approved"])
                self.assertEqual(["paper-1"], current["current_source_ids"])

                # Ingestion rebuilds chunk_evidence_scopes; provenance must survive that replacement.
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "chunk-1", "paper-1", "atrial-fibrillation", ["universal:u1"]
                )))
                after_reingestion = asyncio.run(sdb.get_evidence_scope_review_statuses(
                    "atrial-fibrillation", ["universal:u1"]
                ))["universal:u1"]
                self.assertTrue(after_reingestion["current_approved"])

                asyncio.run(sdb.upsert_paper("paper-2", "p2.pdf", "p2.pdf"))
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "chunk-2", "paper-2", "atrial-fibrillation", ["universal:u1"]
                )))
                stale = asyncio.run(sdb.get_evidence_scope_review_statuses(
                    "atrial-fibrillation", ["universal:u1"]
                ))["universal:u1"]
                self.assertFalse(stale["current_approved"])
                self.assertEqual("source_set_mismatch", stale["reason"])

                with sqlite3.connect(db_path) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM evidence_scope_reviews").fetchone()[0]
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute("UPDATE evidence_scope_reviews SET reason='tampered'")
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute("DELETE FROM evidence_scope_reviews")
            finally:
                sdb.RAG_DB_PATH = original_path
            self.assertEqual(1, count)

    def test_scope_review_requires_exact_nonempty_sources_and_specific_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                base = {
                    "review_batch_id": "demo-review-invalid",
                    "mapping_version": "demo.v1",
                    "topic_key": "atrial-fibrillation",
                    "reviewed_by": "reviewer-fixture",
                    "reviewed_at": "2026-07-16",
                    "review_hash": "sha256:fixture",
                    "slots": [{
                        "slot_key": "*", "decision": "approved_demo", "reason": "No wildcard.",
                        "source_ids": ["paper-1"],
                    }],
                }
                with self.assertRaisesRegex(ValueError, "specific slot"):
                    asyncio.run(sdb.record_evidence_scope_review_batch(base))
                base["slots"][0]["slot_key"] = "universal:u1"
                base["slots"][0]["source_ids"] = []
                with self.assertRaisesRegex(ValueError, "source_ids"):
                    asyncio.run(sdb.record_evidence_scope_review_batch(base))
            finally:
                sdb.RAG_DB_PATH = original_path

    def test_scope_removal_does_not_stale_a_remaining_shared_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            slot_ids = ["AF-UID:universal:u1", "AF-UID:universal:u2"]
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "shared", "paper-af", "atrial-fibrillation", ["universal:u1", "universal:u2"]
                )))
                before = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
                asyncio.run(sdb.update_chunk_evidence_scope(
                    "shared", "atrial-fibrillation", ["universal:u1"]
                ))
                after = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
            finally:
                sdb.RAG_DB_PATH = original_path

            self.assertEqual(before[slot_ids[0]], after[slot_ids[0]])
            self.assertNotEqual(before[slot_ids[1]], after[slot_ids[1]])

    def test_ingestion_normalizes_explicit_topic_and_slot_scope(self):
        chunk = {
            "text": "PMID: 12345678",
            "payload": {"disease": "unknown"},
        }
        result = inject_metadata(chunk, {
            "topic_key": "Atrial Fibrillation",
            "slot_keys": ["AF-UID:universal:u1", "universal:u2"],
        })
        self.assertEqual(result["payload"]["topic_key"], "atrial-fibrillation")
        self.assertEqual(result["payload"]["slot_keys"], ["universal:u1", "universal:u2"])
        self.assertEqual(canonical_topic_key("心房顫動"), "心房顫動")

    def test_ingestion_can_assign_distinct_slot_scope_per_chunk(self):
        external_meta = {
            "topic_key": "atrial-fibrillation",
            "slot_keys": ["*"],
            "slot_keys_by_chunk": {
                "0": ["universal:u1"],
                "paper:chunk:000001": ["universal:u2"],
            },
        }
        first = inject_metadata(
            {"chunk_id": "paper:chunk:000000", "chunk_index": 0, "text": "A", "payload": {}},
            external_meta,
        )
        second = inject_metadata(
            {"chunk_id": "paper:chunk:000001", "chunk_index": 1, "text": "B", "payload": {}},
            external_meta,
        )
        self.assertEqual(first["payload"]["slot_keys"], ["universal:u1"])
        self.assertEqual(second["payload"]["slot_keys"], ["universal:u2"])

    def test_scope_metadata_update_preserves_vector_and_index_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                chunk = self._chunk("legacy", "paper-af", "atrial-fibrillation", ["*"])
                chunk.update({
                    "embedding_status": "embedded",
                    "vector_id": "vector-1",
                    "vector": [1.0, 2.0, 3.0],
                    "indexed_status": "indexed",
                    "qdrant_point_id": "point-1",
                })
                asyncio.run(sdb.upsert_chunk(chunk))
                self.assertTrue(asyncio.run(sdb.update_chunk_evidence_scope(
                    "legacy", "atrial-fibrillation", ["universal:u3-1"],
                )))
                row = asyncio.run(sdb.get_chunks_for_paper("paper-af"))[0]
                payload = json.loads(row["payload_json"])
                with sqlite3.connect(db_path) as conn:
                    scope = conn.execute(
                        "SELECT topic_key, slot_key FROM chunk_evidence_scopes WHERE chunk_id='legacy'"
                    ).fetchone()
            finally:
                sdb.RAG_DB_PATH = original_path

            self.assertEqual(payload["slot_keys"], ["universal:u3-1"])
            self.assertEqual(scope, ("atrial-fibrillation", "universal:u3-1"))
            self.assertEqual(row["embedding_status"], "embedded")
            self.assertEqual(row["indexed_status"], "indexed")
            self.assertEqual(row["vector_id"], "vector-1")
            self.assertEqual(row["qdrant_point_id"], "point-1")

    def test_ingestion_api_rejects_invalid_slot_scope(self):
        with self.assertRaises(HTTPException):
            _external_meta_from_body({"external_meta": {
                "topic_key": "atrial-fibrillation",
                "slot_keys": ["not-a-slot"],
            }})

    def test_init_db_backfills_legacy_disease_as_topic_wildcard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "legacy", "paper-legacy", "atrial-fibrillation", ["universal:u1"]
                )))
                with sqlite3.connect(db_path) as conn:
                    payload = {
                        "disease": "atrial_fibrillation",
                        "embedding_provider": "test",
                        "embedding_model": "embed",
                        "embedding_dim": 3,
                    }
                    conn.execute(
                        "UPDATE chunks SET payload_json=? WHERE chunk_id='legacy'",
                        (json.dumps(payload),),
                    )
                    conn.execute("DELETE FROM chunk_evidence_scopes WHERE chunk_id='legacy'")
                    conn.commit()
                asyncio.run(sdb.init_db())
                with sqlite3.connect(db_path) as conn:
                    scope = conn.execute(
                        "SELECT topic_key, slot_key FROM chunk_evidence_scopes WHERE chunk_id='legacy'"
                    ).fetchone()
            finally:
                sdb.RAG_DB_PATH = original_path
            self.assertEqual(scope, ("atrial-fibrillation", "*"))

    def test_topic_and_global_wildcards_remain_conservative(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            original_path = sdb.RAG_DB_PATH
            try:
                sdb.RAG_DB_PATH = db_path
                asyncio.run(sdb.init_db())
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "af-wide", "paper-af", "atrial-fibrillation", ["*"]
                )))
                asyncio.run(sdb.upsert_chunk(self._chunk(
                    "global", "paper-global", "*", ["*"]
                )))
                slot_ids = ["AF-UID:universal:u1", "AF-UID:universal:u2"]
                before = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "UPDATE chunks SET text = 'AF-wide evidence changed', evidence_updated_at = evidence_updated_at + 10 "
                        "WHERE chunk_id = 'af-wide'"
                    )
                    conn.commit()
                after_topic = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "UPDATE chunks SET text = 'Global evidence changed', evidence_updated_at = evidence_updated_at + 10 "
                        "WHERE chunk_id = 'global'"
                    )
                    conn.commit()
                after_global = asyncio.run(sdb.get_topic_evidence_revisions(
                    "atrial-fibrillation", slot_ids, provider="test", model="embed"
                ))
            finally:
                sdb.RAG_DB_PATH = original_path

            self.assertTrue(all(before[slot] != after_topic[slot] for slot in slot_ids))
            self.assertTrue(all(after_topic[slot] != after_global[slot] for slot in slot_ids))

    def test_indexed_chunk_update_changes_signature_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(pathlib.Path(temp_dir) / "rag.db")
            payload = '{"embedding_provider":"test","embedding_model":"embed","embedding_dim":3}'
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """CREATE TABLE chunks (
                        chunk_id TEXT, paper_id TEXT, payload_json TEXT,
                        updated_at REAL, indexed_status TEXT
                    )"""
                )
                conn.execute(
                    "INSERT INTO chunks VALUES ('chunk-1', 'paper-1', ?, 1.0, 'indexed')",
                    (payload,),
                )
                conn.commit()
            original_path = sdb.RAG_DB_PATH
            try:
                sdb.RAG_DB_PATH = db_path
                before = asyncio.run(sdb.get_indexed_embedding_signatures())[0]["evidence_revision"]
                with sqlite3.connect(db_path) as conn:
                    conn.execute("UPDATE chunks SET updated_at = 2.0 WHERE chunk_id = 'chunk-1'")
                    conn.commit()
                after = asyncio.run(sdb.get_indexed_embedding_signatures())[0]["evidence_revision"]
            finally:
                sdb.RAG_DB_PATH = original_path
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
