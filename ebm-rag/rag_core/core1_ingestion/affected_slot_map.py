# 模組定位: AF Topic 的人工審核 affected-slot mapping 載入與契約驗證器。
# 主要責任: 驗證 100 個自有 af-* slot、解析可生成 evidence sets，並建立 chunk 到 slot 的反向索引。
# 呼叫來源: AF corpus 準備工具、RAG 驗收測試與後續 evidence source admin。
# 輸入契約: 同目錄版本化 JSON；每個 slot 恰好一筆且只引用已宣告 evidence set。
# 輸出契約: 正規化 mapping、generation-eligible chunk scope 反向索引與穩定 mapping hash。
# 安全邊界: 不用 LLM/關鍵字推測臨床歸屬；partial/gap/excluded 絕不進可生成 scope。
# 維護提醒: headings 或 chunk 邊界改版時先人工重審 JSON，再提升 mapping_version。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_MAP_PATH = Path(__file__).with_name("atrial_fibrillation_affected_slots.v1.json")
DEFAULT_DEMO_REVIEW_PATH = Path(__file__).with_name("atrial_fibrillation_demo_scope_review.v1.json")
ALLOWED_REVIEW_STATUSES = {"covered", "partial", "gap", "metadata_only", "excluded_proprietary"}
DEMO_CORE_SLOT_SOURCES = {
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


def _stable_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_demo_scope_review(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user-provided demo review without inferring clinical scope."""
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ValueError("demo scope review schema mismatch")
    if data.get("topic_key") != "atrial-fibrillation" or data.get("purpose") != "buyer_demo":
        raise ValueError("demo scope review topic/purpose mismatch")
    for key in ("review_batch_id", "mapping_version", "reviewed_by"):
        if not str(data.get(key) or "").strip():
            raise ValueError(f"demo scope review requires {key}")
    try:
        date.fromisoformat(str(data.get("reviewed_at") or ""))
    except ValueError as exc:
        raise ValueError("demo scope review requires ISO reviewed_at") from exc
    if data.get("clinical_reconfirmation_required") is not True:
        raise ValueError("demo scope review must retain clinical reconfirmation gate")
    if data.get("forbid_wildcard_scope") is not True:
        raise ValueError("demo scope review must forbid wildcard scope")

    forbidden = data.get("forbidden_source_ids")
    if not isinstance(forbidden, list) or not {"AR", "RM_76EA6AB0"}.issubset(set(forbidden)):
        raise ValueError("demo scope review must preserve forbidden source IDs")
    slots = data.get("slots")
    if not isinstance(slots, list):
        raise ValueError("demo scope review requires slots")
    observed_keys = [str(item.get("slot_key") or "") for item in slots if isinstance(item, dict)]
    if observed_keys != list(DEMO_CORE_SLOT_SOURCES):
        raise ValueError("demo scope review must contain exactly the reviewed eight slots")

    forbidden_set = set(forbidden) | {"*"}
    for item in slots:
        slot_key = item["slot_key"]
        expected_heading, expected_sources = DEMO_CORE_SLOT_SOURCES[slot_key]
        source_ids = item.get("source_ids")
        if item.get("heading") != expected_heading:
            raise ValueError(f"demo scope review heading mismatch: {slot_key}")
        if item.get("decision") != "approved_demo":
            raise ValueError(f"demo scope review decision mismatch: {slot_key}")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"demo scope review requires reason: {slot_key}")
        if not isinstance(source_ids, list) or len(source_ids) != len(set(source_ids)):
            raise ValueError(f"demo scope review sources must be unique: {slot_key}")
        if set(source_ids) & forbidden_set:
            raise ValueError(f"demo scope review contains forbidden source: {slot_key}")
        if set(source_ids) != expected_sources:
            raise ValueError(f"demo scope review source set mismatch: {slot_key}")

    normalized = dict(data)
    normalized["review_hash"] = _stable_hash(data)
    return normalized


def load_demo_scope_review(path: Path | None = None) -> dict[str, Any]:
    """Load the immutable eight-slot buyer-demo review artifact."""
    review_path = path or DEFAULT_DEMO_REVIEW_PATH
    return validate_demo_scope_review(json.loads(review_path.read_text(encoding="utf-8")))


def enforce_demo_scope_review(paper_id: str, slot_keys: list[str]) -> list[str]:
    """Remove unapproved sources from the eight reviewed demo slots, preserving other mappings."""
    allowed_by_slot = {
        slot_key: sources
        for slot_key, (_heading, sources) in DEMO_CORE_SLOT_SOURCES.items()
    }
    # ponytail: This is intentionally a removal-only gate; new scope still requires an explicit reviewed map.
    return [
        slot_key for slot_key in dict.fromkeys(slot_keys)
        if slot_key not in allowed_by_slot or paper_id in allowed_by_slot[slot_key]
    ]


def load_affected_slot_map(path: Path | None = None) -> tuple[dict[str, Any], dict[tuple[str, int], list[str]]]:
    """Load the reviewed mapping and return only generation-safe chunk scopes."""
    map_path = path or DEFAULT_MAP_PATH
    data = json.loads(map_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0" or data.get("topic_key") != "atrial-fibrillation":
        raise ValueError("affected-slot map schema/topic mismatch")
    slot_namespace = data.get("slot_namespace")
    if slot_namespace not in {"universal", "custom"}:
        raise ValueError("affected-slot map requires an explicit universal/custom slot_namespace")

    evidence_sets = data.get("evidence_sets")
    slots = data.get("slots")
    if not isinstance(evidence_sets, dict) or not isinstance(slots, list):
        raise ValueError("affected-slot map requires evidence_sets and slots")

    keys = [str(item.get("slot_key") or "") for item in slots if isinstance(item, dict)]
    if len(keys) != 100 or len(set(keys)) != 100 or any(not key.startswith("af-") for key in keys):
        raise ValueError("affected-slot map must contain exactly 100 unique af-* slots")

    inverse: dict[tuple[str, int], set[str]] = {}
    for item in slots:
        status = item.get("review_status")
        set_ids = item.get("evidence_set_ids", [])
        eligible = item.get("generation_eligible") is True
        if status not in ALLOWED_REVIEW_STATUSES or not isinstance(set_ids, list):
            raise ValueError(f"invalid review contract for {item.get('slot_key')}")
        if eligible and status != "covered":
            raise ValueError(f"only covered slots may be generation eligible: {item['slot_key']}")
        if not eligible:
            continue
        for set_id in set_ids:
            selectors = evidence_sets.get(set_id)
            if not isinstance(selectors, list) or not selectors:
                raise ValueError(f"unknown or empty evidence set {set_id!r}")
            for selector in selectors:
                paper_id = str(selector.get("paper_id") or "")
                indexes = selector.get("chunk_indexes")
                if not paper_id or not isinstance(indexes, list) or not indexes:
                    raise ValueError(f"invalid selector in evidence set {set_id!r}")
                for chunk_index in indexes:
                    if not isinstance(chunk_index, int) or chunk_index < 0:
                        raise ValueError(f"invalid chunk index in evidence set {set_id!r}")
                    inverse.setdefault((paper_id, chunk_index), set()).add(f"{slot_namespace}:{item['slot_key']}")

    data["mapping_hash"] = _stable_hash(data)
    normalized_inverse = {key: sorted(values) for key, values in inverse.items()}
    return data, normalized_inverse
