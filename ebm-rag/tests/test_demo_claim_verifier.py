import pathlib
import sys
import unittest


EBM_RAG_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from rag_core.core4_ragging.demo_verifier import _deterministic_claim_verify


class DeterministicClaimVerifierTest(unittest.TestCase):
    def test_cited_disease_metadata_counts_when_source_uses_abbreviation(self):
        chunks = [{
            "chunk_id": "af-guideline-1",
            "doi": "10.test/af",
            "disease": "atrial_fibrillation",
            "text": "Anticoagulation reduces stroke risk in patients with AF.",
        }]
        ebm_hits = {
            "rag_comments": [{
                "comment": "Retrieved guideline evidence supports anticoagulation for stroke prevention for atrial fibrillation.",
                "sources": [{"chunk_id": "af-guideline-1", "doi": "10.test/af"}],
            }]
        }

        result = _deterministic_claim_verify(chunks, ebm_hits, "provider unavailable")

        self.assertEqual(result["overall_claim_support"], 1.0)
        self.assertEqual(result["unsupported_claim_count"], 0)


if __name__ == "__main__":
    unittest.main()
