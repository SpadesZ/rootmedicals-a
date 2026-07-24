from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "llmxx-server"))
sys.path.insert(0, str(REPO_ROOT / "llmxx-client" / "apps" / "thin-capture-client" / "src"))

from llmxx_client_thin_capture.doctor_alert_widget import _clinical_comment, _clinical_guidance_lines, _extract_evidence_lines
from llmxx_client_thin_capture.payload import (
    AF_ACTIVE_BLEEDING_FIXTURE_ID,
    ALLERGIC_RHINITIS_FIXTURE_ID,
    _select_demo_fixture_id,
)
from server_app.contracts.schemas import ClinicalParse, FormalClientPayload, ScreenshotClientPayload, SoapPayload
from server_app.core.demo_fixtures import build_demo_ebm_fixture
from server_app.core.response_builder import build_success_response
from server_app.infra.errors import get_error
from server_app.ocr.server_ocr import _ServerOCR, screenshot_payload_to_formal


class DemoPhysicianAlertTest(unittest.TestCase):
    @staticmethod
    def _fixture(fixture_id: str, clinical: ClinicalParse) -> dict:
        payload = FormalClientPayload(
            schema_version="llmxx-client-screenshot.v0.1",
            soap=SoapPayload(S="demo", O=clinical.hx, A=clinical.dx_text, P=clinical.tx),
            icd10_code=clinical.icd10_code,
            demo_mode="demo_fixture",
            demo_fixture_id=fixture_id,
        )
        result = build_demo_ebm_fixture(payload, clinical, enabled=True)
        assert result is not None
        return result

    def test_green_and_contraindication_orange_are_traceable_and_physician_facing(self) -> None:
        orange_metadata = {
            "normalized_diagnosis": "atrial fibrillation",
            "soap": {
                "O": "active gastrointestinal bleeding",
                "A": "atrial fibrillation",
                "P": "start apixaban immediately",
            },
        }
        self.assertEqual(
            _select_demo_fixture_id(ALLERGIC_RHINITIS_FIXTURE_ID, orange_metadata),
            AF_ACTIVE_BLEEDING_FIXTURE_ID,
        )

        green_clinical = ClinicalParse(
            dx="allergic rhinitis",
            tx="intranasal corticosteroid spray",
            hx="nasal congestion",
            icd_code="J30.9",
            icd10_code="J30.9",
            diagnosis_label="Allergic rhinitis, unspecified",
            dx_text="allergic rhinitis",
        )
        green = self._fixture(ALLERGIC_RHINITIS_FIXTURE_ID, green_clinical)
        green_evidence = _extract_evidence_lines(green, "zh")[0]
        self.assertIn("Rhinitis 2020: A practice parameter update", green_evidence)
        self.assertIn("PMID: 32707227", green_evidence)
        self.assertNotIn("paper_id", green_evidence)
        self.assertNotIn("chunk_id", green_evidence)

        orange_clinical = ClinicalParse(
            dx="atrial fibrillation",
            tx="start apixaban immediately for stroke prevention",
            hx="active gastrointestinal bleeding suspected; hemoglobin 8.2 g/dL",
            icd_code="I48.91",
            icd10_code="I48.91",
            diagnosis_label="Unspecified atrial fibrillation",
            dx_text="atrial fibrillation with active gastrointestinal bleeding",
        )
        orange = self._fixture(AF_ACTIVE_BLEEDING_FIXTURE_ID, orange_clinical)
        response = build_success_response(
            session_id="server-test",
            client_session_id="client-test",
            correlation_id="correlation-test",
            clinical=orange_clinical,
            ebm_hits=orange,
            adjudication={},
        )
        self.assertEqual(response["final_gate"]["light_color"], "orange")
        self.assertIn("rag_orange_hard_gate", response["final_gate"]["hard_fail_reasons"])
        orange_evidence = _extract_evidence_lines(response["ebm"], "zh")[0]
        self.assertIn("2023 ACC/AHA/ACCP/HRS Guideline", orange_evidence)
        self.assertIn("PMID: 38033089", orange_evidence)
        guidance = _clinical_guidance_lines(response["clinical_parse"], response["final_gate"], "orange", "zh")
        self.assertTrue(any("活動性腸胃道出血" in line for line in guidance["clues"]))

    def test_disabled_demo_fixture_is_a_red_workflow_error_not_yellow(self) -> None:
        spec = get_error("demo_fixture_disabled")
        self.assertEqual(spec.http_status, 409)
        self.assertEqual(spec.status, "failed")
        comment = _clinical_comment(
            lang="zh",
            light="red",
            final_gate={"light_color": "red", "reason_codes": ["demo_fixture_disabled"]},
            ebm={},
            response={"error_code": "demo_fixture_disabled"},
        )
        self.assertIn("未啟用 Demo Fixture", comment)

    def test_complete_his_metadata_does_not_require_ocr_runtime(self) -> None:
        payload = ScreenshotClientPayload.model_validate(
            {
                "schema_version": "llmxx-client-screenshot.v0.1",
                "screenshot": {
                    "format": "png",
                    "image_b64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                    "width": 1,
                    "height": 1,
                },
                "clinical_metadata": {
                    "icd10_code": "I48.91",
                    "diagnosis_label": "Unspecified atrial fibrillation",
                    "normalized_diagnosis": "atrial fibrillation",
                    "soap": {
                        "S": "black stools and dizziness",
                        "O": "active gastrointestinal bleeding; HR 112",
                        "A": "atrial fibrillation with active gastrointestinal bleeding",
                        "P": "start apixaban immediately",
                    },
                    "vital_signs": {"hr": "112"},
                    "metadata_source": "clinicalguard_standalone",
                },
            }
        )
        with patch.object(_ServerOCR, "extract_fields", side_effect=AssertionError("OCR must not run")):
            formal = screenshot_payload_to_formal(payload)
        self.assertEqual(formal.icd10_code, "I48.91")
        self.assertEqual(formal.soap.P, "start apixaban immediately")


if __name__ == "__main__":
    unittest.main()
