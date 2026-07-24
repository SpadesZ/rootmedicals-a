import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, patch


EBM_RAG_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from rag_core.core4_ragging import pipeline
from lava.matching_tasks.synthetic_ebm_candidate import _deterministic_candidate


class LiveSyntheticFallbackTest(unittest.TestCase):
    def test_active_bleeding_prefers_traceable_contraindication_and_normalizes_grade(self):
        general = {"chunk_id": "general-1", "doi": "10.test/general", "score": 0.8, "six_s_level": "Summaries", "ocebm_level": "Level_1", "grade_baseline": "A", "text": "Anticoagulation prevents stroke."}
        contraindication = {"chunk_id": "contra-1", "doi": "10.test/contra", "score": 0.7, "six_s_level": "System", "ocebm_level": "Level_1", "grade_baseline": "A", "has_contraindication_terms": True, "text": "Unless an absolute contraindication to anticoagulation is present, decisions should balance bleeding against stroke risk."}

        result = _deterministic_candidate({
            "dx_summary": "atrial fibrillation",
            "case_context": {"dx": "atrial fibrillation", "tx": "start apixaban immediately", "hx": "active gastrointestinal bleeding"},
            "chunks": [general, contraindication],
        }, "provider unavailable")

        self.assertEqual(result["light_color"], "orange")
        self.assertEqual(result["rag_comments"][0]["grade"], "Grade_A")
        self.assertEqual(result["rag_comments"][0]["sources"][0]["chunk_id"], "contra-1")

    def test_verifier_rejection_runs_existing_synthetic_candidate(self):
        chunks = [{"chunk_id": "contra-1", "doi": "10.test/guideline"}]
        generated = {
            "light_color": "yellow",
            "llmaaj_score": 80,
            "short_comment": "retrieved candidate",
            "rag_comments": [{
                "topic": "contraindication",
                "comment": "Active bleeding requires review.",
                "evidence_level": "Level_1",
                "grade": "Grade_A",
                "sources": [{"chunk_id": "contra-1", "doi": "10.test/guideline"}],
            }],
            "alternatives": [],
            "warnings": [],
        }
        synthetic = {"status": "ok", "light_color": "orange", "demo_candidate_kind": "synthetic_ebm_candidate"}
        verifier_rejection = {
            "status": "not_evaluable",
            "error": "Demo verifier gate rejected EBM output",
            "demo_verifier": {"hard_fail_reasons": ["unsupported_claims_present"]},
        }

        with (
            patch.object(pipeline, "demo_synthetic_fallback_enabled", return_value=True),
            patch.object(pipeline, "retrieve", new=AsyncMock(return_value={"status": "ok", "hits": chunks})),
            patch.object(pipeline, "summarize_chunk_quality", return_value={"query_safe": True}),
            patch("lava.matching_tasks.ebm_generate.execute_ebm_generate", new=AsyncMock(return_value={"status": "ok", "ebm_hits": generated})),
            patch.object(pipeline, "_apply_demo_verifier_gate", new=AsyncMock(return_value=verifier_rejection)),
            patch.object(pipeline, "_run_synthetic_demo_candidate", new=AsyncMock(return_value=synthetic)) as fallback,
            patch.object(pipeline.sdb, "save_retrieval_log", new=AsyncMock()),
        ):
            result = asyncio.run(pipeline.run_query("atrial fibrillation", filters={"demo_synthetic_fallback": True}))

        self.assertIs(result, synthetic)
        fallback.assert_awaited_once()
        self.assertEqual(fallback.await_args.kwargs["failure_reason"], "unsupported_claims_present")


if __name__ == "__main__":
    unittest.main()
