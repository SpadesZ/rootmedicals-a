# 模組定位: ebm-rag source policy registry 與 release gate 的最小 contract suite。
# 主要責任: 驗證 AF paper coverage、缺 policy、權利未核准、withdrawn 與核准商用路徑。
# 呼叫來源: 本機 unittest、Phase source-lifecycle gate 與 release verification。
# 輸入契約: repo policy fixture 與純記憶體 source policy objects；不呼叫網路。
# 輸出契約: deterministic pass/fail，任何權利資訊不完整均維持 fail-closed。
# 安全邊界: 測試不得把 permission_required 或 review_required 誤升為 approved。
# 維護提醒: 新增 AF paper 時 coverage test 必須先紅，再補人工審核 policy。
# ----------------------------------------------------------------------------------------------------

import pathlib
import sys
import unittest


EBM_RAG_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from rag_core.core1_ingestion.source_policy import (
    POLICY_SCHEMA,
    PUBLIC_SOURCE_DETAIL_SCHEMA,
    build_public_source_details,
    evaluate_source_use,
    load_source_policy_registry,
)
from rag_core.core5_api.schemas import SourceDetailsRequest, SourceUseGateRequest


AF_PAPERS = {
    "RM_AF_GUIDE_2023", "RM_AF_CONTRA_2023", "RM_AF_DEFINITIONS_PATHOPHYS_2023",
    "RM_AF_EPIDEMIOLOGY_2023", "RM_AF_EVALUATION_2023", "RM_AF_RISK_MODIFICATION_2023",
    "RM_AF_ANTICOAG_2023", "RM_AF_RATE_CONTROL_2023", "RM_AF_RHYTHM_CONTROL_2023",
}


class SourcePolicyTests(unittest.TestCase):
    def test_af_registry_covers_every_prepared_paper_and_blocks_commercial_use(self):
        policies = load_source_policy_registry()
        self.assertEqual(set(policies), AF_PAPERS)
        gate = evaluate_source_use(policies, sorted(AF_PAPERS), "commercial_publication")
        self.assertFalse(gate["allowed"])
        self.assertEqual({item["reason"] for item in gate["blocked"]}, {"license_permission_required"})

    def test_missing_and_withdrawn_source_fail_closed(self):
        policy = self._approved_policy("withdrawn-paper")
        policy["lifecycle_status"] = "withdrawn"
        gate = evaluate_source_use(
            {"withdrawn-paper": policy}, ["missing", "withdrawn-paper"], "commercial_publication",
        )
        self.assertEqual(
            gate["blocked"],
            [
                {"paper_id": "missing", "reason": "source_policy_missing"},
                {"paper_id": "withdrawn-paper", "reason": "source_withdrawn"},
            ],
        )

    def test_explicit_approved_commercial_policy_passes(self):
        policy = self._approved_policy("paper-1")
        self.assertTrue(
            evaluate_source_use({"paper-1": policy}, ["paper-1"], "commercial_publication")["allowed"]
        )

    def test_source_use_request_rejects_duplicates_and_unknown_use(self):
        with self.assertRaises(ValueError):
            SourceUseGateRequest(paper_ids=["paper-1", "paper-1"], required_use="commercial_publication")
        with self.assertRaises(ValueError):
            SourceUseGateRequest(paper_ids=["paper-1"], required_use="scrape_anything")

    def test_public_source_details_are_allowlisted_and_fail_closed(self):
        policy = self._approved_policy("paper-1")
        policy.update({
            "organization": "Example Society", "publication_year": 2026,
            "doi": "10.1000/example", "source_url": "https://example.test/paper",
        })
        details = build_public_source_details({
            "paper-1": {
                "guideline_title": "Reviewed source", "source_policy": policy,
                "source_pdf_path": "C:/secret/source.pdf", "api_key": "must-not-leak",
                "ocr_raw_text": "must-not-leak",
            }
        }, ["paper-1", "missing"])
        self.assertEqual(details["schema"], PUBLIC_SOURCE_DETAIL_SCHEMA)
        self.assertEqual(details["missing_paper_ids"], ["missing"])
        self.assertEqual(details["sources"][0]["organization"], "Example Society")
        self.assertTrue(details["sources"][0]["commercial_publication_allowed"])
        serialized = str(details)
        for forbidden in ("source_pdf_path", "api_key", "ocr_raw_text", "must-not-leak"):
            self.assertNotIn(forbidden, serialized)

    def test_source_details_request_is_bounded_and_unique(self):
        self.assertEqual(SourceDetailsRequest(paper_ids=["paper-1"]).paper_ids, ["paper-1"])
        with self.assertRaises(ValueError):
            SourceDetailsRequest(paper_ids=["paper-1", "paper-1"])

    @staticmethod
    def _approved_policy(paper_id):
        return {
            "schema": POLICY_SCHEMA,
            "paper_id": paper_id,
            "document_version": "v1",
            "lifecycle_status": "current",
            "license_status": "approved",
            "permitted_uses": ["internal_validation", "retrieval_generation", "commercial_publication"],
            "commercial_publication_allowed": True,
            "rights_source_url": "https://example.test/rights",
            "rights_reviewed_at": "2026-07-16",
        }


if __name__ == "__main__":
    unittest.main()
