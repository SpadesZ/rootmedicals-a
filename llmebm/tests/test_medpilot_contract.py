# 模組定位: Medpilot 單輪 EBM 問答的 deterministic contract tests。
# 主要責任: 鎖定招呼語不呼叫 RAG、臨床回答必須逐段有 retrieved chunk citation，以及證據不足時 fail closed。
# 呼叫來源: 開發者本機 unittest discover 與 Medpilot 交付驗收。
# 輸入契約: 合成的 RAG ebm_hits payload；不讀 production DB、不呼叫外部 LLM。
# 輸出契約: 回應只允許 smalltalk、evidence_answer、insufficient_evidence 三種可解釋結果。
# 安全邊界: 任一臨床段落缺 citation、citation 不在 retrieval chunks 或模型聲稱缺直接證據時不得顯示臨床答案。
# 維護提醒: 若 RAG schema 改版，先更新此 trust-boundary 測試再改 browser renderer。
# ----------------------------------------------------------------------------------------------------

import sys
import unittest
from pathlib import Path


LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

from app.medpilot import build_medpilot_response, smalltalk_response


def evidence_payload():
    return {
        "status": "ok",
        "query_id": "query-1",
        "light_color": "yellow",
        "short_comment": "Evidence supports risk-based stroke prevention.",
        "rag_comments": [
            {
                "topic": "Stroke prevention",
                "comment": "Use validated stroke-risk assessment and reassess periodically.",
                "evidence_level": "Level_1",
                "grade": "Grade_A",
                "sources": [
                    {
                        "chunk_id": "RM_AF_GUIDE_2023:chunk:000014",
                        "doi": "10.1016/j.jacc.2023.08.017",
                        "score": 0.75,
                    }
                ],
            }
        ],
        "warnings": [{"code": "icd_missing", "message": "Clinical review is required."}],
        "retrieval": {
            "chunks": [
                {
                    "chunk_id": "RM_AF_GUIDE_2023:chunk:000014",
                    "paper_id": "RM_AF_GUIDE_2023",
                    "doi": "10.1016/j.jacc.2023.08.017",
                    "payload": {
                        "guideline_title": "2023 ACC/AHA/ACCP/HRS Guideline for Atrial Fibrillation",
                        "citation_text": "Joglar JA et al. 2023 AF guideline.",
                    },
                }
            ]
        },
        "model": {"provider": "google", "model_id": "gemini-2.5-flash"},
    }


class MedpilotContractTests(unittest.TestCase):
    def test_browser_and_server_are_wired_to_real_medpilot_endpoint(self):
        server = (LLMEBM_ROOT / "main_ebm.py").read_text(encoding="utf-8")
        script = (LLMEBM_ROOT / "app" / "static" / "js" / "panels_split.js").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/v1/medpilot/query")', server)
        self.assertIn('"/api/v1/rag/query"', server)
        self.assertIn("fetch('/api/v1/medpilot/query'", script)
        self.assertIn('/static/css/style.css?v=20260718-medpilot-1', (
            LLMEBM_ROOT / "app" / "templates" / "index.html"
        ).read_text(encoding="utf-8"))
        self.assertNotIn("Parsing current clinical guidelines from the EBM database", script)
        self.assertNotIn("userDiv.innerHTML", script)
        self.assertIn("document.createTextNode(text)", script)

    def test_smalltalk_is_deterministic_and_has_no_citations(self):
        result = smalltalk_response("哈囉！")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["kind"], "smalltalk")
        self.assertEqual(result["citations"], [])
        self.assertFalse(result["rag_called"])

    def test_evidence_answer_keeps_only_retrieved_traceable_citations(self):
        result = build_medpilot_response(evidence_payload())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["kind"], "evidence_answer")
        self.assertEqual(result["query_id"], "query-1")
        self.assertEqual(result["sections"][0]["citation_indexes"], [1])
        self.assertEqual(result["citations"][0]["paper_id"], "RM_AF_GUIDE_2023")
        self.assertEqual(result["citations"][0]["chunk_id"], "RM_AF_GUIDE_2023:chunk:000014")
        self.assertEqual(result["citations"][0]["doi"], "10.1016/j.jacc.2023.08.017")

    def test_unretrieved_citation_fails_closed(self):
        payload = evidence_payload()
        payload["rag_comments"][0]["sources"][0]["chunk_id"] = "invented:chunk:1"
        result = build_medpilot_response(payload)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["kind"], "insufficient_evidence")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["sections"], [])

    def test_lacking_direct_evidence_fails_closed(self):
        payload = evidence_payload()
        payload["rag_comments"][0]["comment"] = "lacking direct evidence"
        result = build_medpilot_response(payload)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["citations"], [])

    def test_reworded_no_direct_evidence_also_fails_closed(self):
        # 2026-09-10 實測回歸：模型沒有照 prompts.py 規則 5 逐字輸出 marker，
        # 而是改寫語序，舊版字面比對整組落空，於是缺證據的回答被當成臨床答案顯示。
        reworded = [
            "Direct evidence is lacking regarding starting anticoagulation in atrial fibrillation.",
            "Direct evidence for this plan was insufficient.",
            "There is no strong direct evidence supporting this approach.",
            "We found an absence of direct evidence for this comparison.",
            "本地索引缺乏直接證據支持此處置。",
            "直接證據不足，需由醫師判斷。",
        ]
        for comment in reworded:
            with self.subTest(comment=comment):
                payload = evidence_payload()
                payload["rag_comments"][0]["comment"] = comment
                result = build_medpilot_response(payload)
                self.assertEqual(result["status"], "insufficient_evidence")
                self.assertEqual(result["sections"], [])

    def test_evidence_positive_wording_is_not_mistaken_for_absence(self):
        # 守衛偏嚴是刻意的，但不能嚴到把「有證據」的句子也擋掉。
        for comment in [
            "The trial showed no benefit; direct evidence supports rate control here.",
            "Direct evidence from two randomised trials supports this plan.",
            "No adverse events were reported, and direct evidence is consistent.",
        ]:
            with self.subTest(comment=comment):
                payload = evidence_payload()
                payload["rag_comments"][0]["comment"] = comment
                result = build_medpilot_response(payload)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["kind"], "evidence_answer")

    def test_response_carries_no_traffic_light(self):
        # 燈號需要病歷/ICD/處置 context；自由問答沒有，ebm-rag 一律回 yellow + icd_missing。
        # 那不是證據品質判斷，瀏覽器也從未渲染它，因此不放進 Medpilot 契約。
        for result in (build_medpilot_response(evidence_payload()), smalltalk_response("hello")):
            self.assertNotIn("light_color", result)

    def test_non_ok_rag_status_fails_closed_without_provider_detail(self):
        result = build_medpilot_response({
            "status": "not_evaluable",
            "error": "provider key abc123 failed",
            "rag_comments": [],
            "retrieval": {"chunks": []},
        })
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertNotIn("abc123", result["answer"])


if __name__ == "__main__":
    unittest.main()
