import pathlib
import sys
import unittest


EBM_RAG_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(EBM_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(EBM_RAG_ROOT))

from rag_core.core4_ragging.traffic_light import requires_contraindication_hard_gate


class PatientContraindicationNegationTest(unittest.TestCase):
    def test_negated_active_bleeding_does_not_trigger_hard_gate(self):
        self.assertFalse(
            requires_contraindication_hard_gate([], {"hx": "No active bleeding."})
        )

    def test_denied_gastrointestinal_bleeding_does_not_trigger_hard_gate(self):
        self.assertFalse(
            requires_contraindication_hard_gate([], {"hx": "Denies active gastrointestinal bleeding."})
        )

    def test_positive_active_bleeding_still_triggers_hard_gate(self):
        self.assertTrue(
            requires_contraindication_hard_gate([], {"hx": "Active gastrointestinal bleeding today."})
        )

    def test_structured_positive_flag_still_triggers_hard_gate(self):
        self.assertTrue(
            requires_contraindication_hard_gate([], {"active_bleeding": True, "hx": "No bleeding noted."})
        )


if __name__ == "__main__":
    unittest.main()
