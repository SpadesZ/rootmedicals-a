# 模組定位: AF affected-slot mapping 的獨立、無第三方依賴契約測試。
# 主要責任: 阻擋 SQL af-* headings 與版本化 mapping 漏列、重複或錯誤放行。
# 呼叫來源: 開發者本機驗收、CI 與 100-slot corpus mapping 施工包 gate。
# 輸入契約: migration 002 SQL、mapping JSON 與 affected_slot_map 共用驗證器。
# 輸出契約: 任一 slot 集合不一致、selector 非法或 fail-closed 規則破壞即測試失敗。
# 安全邊界: 僅讀 repo artifact；不連外、不改 DB、不呼叫 embedding/LLM。
# 維護提醒: 新增或改名 af-* heading 時必須同步人工審核 mapping 才能過 gate。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "ebm-rag" / "rag_core" / "core1_ingestion" / "affected_slot_map.py"
MAP_PATH = MODULE_PATH.with_name("atrial_fibrillation_affected_slots.v1.json")
DEMO_REVIEW_PATH = MODULE_PATH.with_name("atrial_fibrillation_demo_scope_review.v1.json")
MIGRATION_PATH = ROOT / "llmebm" / "app" / "model" / "migrations" / "002_atrial_fibrillation_hierarchy.sql"
PREPARE_PATH = ROOT / "ebm-rag" / "tools" / "prepare_af_topic_corpus.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("affected_slot_map", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class AffectedSlotMapTests(unittest.TestCase):
    def test_mapping_exactly_matches_sql_af_slots(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        sql_keys = set(re.findall(r"'((?:af-)[a-z0-9-]+)'", sql))
        data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        map_keys = {item["slot_key"] for item in data["slots"]}
        self.assertEqual(100, len(sql_keys))
        self.assertEqual(sql_keys, map_keys)

    def test_only_covered_slots_enter_generation_scope(self):
        data, inverse = _load_module().load_affected_slot_map(MAP_PATH)
        eligible = {item["slot_key"] for item in data["slots"] if item["generation_eligible"]}
        namespace = data["slot_namespace"]
        self.assertEqual("universal", namespace)
        self.assertTrue(all(slot.startswith(f"{namespace}:") for slots in inverse.values() for slot in slots))
        scoped = {slot.split(":", 1)[1] for slots in inverse.values() for slot in slots}
        self.assertEqual(eligible, scoped)
        self.assertTrue(all(item["review_status"] == "covered" for item in data["slots"] if item["generation_eligible"]))
        self.assertTrue(data["mapping_hash"].startswith("sha256:"))

    def test_demo_scope_review_freezes_corrected_eight_slots(self):
        module = _load_module()
        data = module.load_demo_scope_review(DEMO_REVIEW_PATH)
        expected = {
            "universal:u1": ("Overview and Recommendations", {"RM_AF_GUIDE_2023"}),
            "universal:u2-1": ("Description", {"RM_AF_DEFINITIONS_PATHOPHYS_2023"}),
            "universal:u3": ("Diagnosis", {"RM_AF_EVALUATION_2023"}),
            "universal:u3-1": ("Making the Diagnosis", {"RM_AF_EVALUATION_2023"}),
            "universal:u4": (
                "Management",
                {
                    "RM_AF_ANTICOAG_2023", "RM_AF_CONTRA_2023",
                    "RM_AF_RATE_CONTROL_2023", "RM_AF_RHYTHM_CONTROL_2023",
                },
            ),
            "universal:u5-2-1": ("United States Guidelines", {"RM_AF_GUIDE_2023"}),
            "universal:af-mgmt-thromboembolic": (
                "Thromboembolic Prophylaxis", {"RM_AF_ANTICOAG_2023", "RM_AF_CONTRA_2023"},
            ),
            "universal:af-prognosis-stroke": (
                "Embolic Stroke and Thromboembolism", {"RM_AF_ANTICOAG_2023", "RM_AF_CONTRA_2023"},
            ),
        }
        observed = {
            item["slot_key"]: (item["heading"], set(item["source_ids"]))
            for item in data["slots"]
        }
        self.assertEqual(expected, observed)
        self.assertNotIn("universal:u4-2", observed)
        self.assertNotIn("universal:u5-2", observed)
        self.assertTrue(data["clinical_reconfirmation_required"])
        self.assertTrue(data["review_hash"].startswith("sha256:"))

        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        for slot_key, (heading, _sources) in expected.items():
            node_key = slot_key.split(":", 1)[1]
            self.assertIn(f"'{node_key}'", sql)
            self.assertIn(f"'{heading}'", sql)

    def test_demo_scope_review_rejects_wildcard_or_forbidden_source(self):
        module = _load_module()
        data = json.loads(DEMO_REVIEW_PATH.read_text(encoding="utf-8"))
        data["slots"][0]["source_ids"] = ["RM_76EA6AB0"]
        with self.assertRaisesRegex(ValueError, "forbidden source"):
            module.validate_demo_scope_review(data)
        data = json.loads(DEMO_REVIEW_PATH.read_text(encoding="utf-8"))
        data["slots"][0]["slot_key"] = "*"
        with self.assertRaisesRegex(ValueError, "exactly the reviewed eight slots"):
            module.validate_demo_scope_review(data)

    def test_demo_scope_review_filters_only_reviewed_core_slots(self):
        module = _load_module()
        self.assertEqual(
            ["universal:u1", "universal:u5-2-1", "universal:non-demo"],
            module.enforce_demo_scope_review(
                "RM_AF_GUIDE_2023",
                [
                    "universal:u1", "universal:u2-1", "universal:u4",
                    "universal:u5-2-1", "universal:non-demo",
                ],
            ),
        )
        self.assertEqual(
            ["universal:non-demo"],
            module.enforce_demo_scope_review(
                "RM_AF_RISK_MODIFICATION_2023", ["universal:u4", "universal:non-demo"],
            ),
        )
        self.assertEqual(
            ["universal:u4"],
            module.enforce_demo_scope_review("RM_AF_ANTICOAG_2023", ["universal:u4"]),
        )

    def test_prepare_tool_enforces_reviewed_scope_and_current_guideline_slot(self):
        source = PREPARE_PATH.read_text(encoding="utf-8")
        self.assertIn("enforce_demo_scope_review", source)
        self.assertIn('GUIDELINES = ["universal:u5", "universal:u5-2-1"]', source)
        self.assertNotIn('GUIDELINES = ["universal:u5", "universal:u5-2"]', source)


if __name__ == "__main__":
    unittest.main()
