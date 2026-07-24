# 模組定位: llmebm 與授權醫學參考頁之間的去識別結構能力比較工具。
# 主要責任: 驗證 bounded capture、推導一般性 UI 能力、輸出 comparison 與人工核准 import plan。
# 呼叫來源: 開發期一次性 Chrome reference audit 與 unittest contract；不屬於正式 runtime。
# 輸入契約: 兩份 reference-layout-capture.v1 JSON，只含計數、ARIA/landmark 類型與 screenshot hash。
# 輸出契約: comparison/import-plan JSON；只列能力決策，不含文字、URL、像素尺寸或原始 DOM。
# 安全邊界: 不連網、不讀 cookie/storage、不開 production DB、不套用 hierarchy 或 UI 變更。
# 維護提醒: 新增 capture 欄位前必須先證明不會保存來源內容或視覺識別，並補拒絕測試。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


CAPTURE_SCHEMA = "reference-layout-capture.v1"
COMPARISON_SCHEMA = "llmebm-reference-layout-comparison.v1"
IMPORT_PLAN_SCHEMA = "llmebm-reference-import-plan.v1"
SOURCE_FAMILIES = {"llmebm-current", "authenticated-medical-reference"}
PAGE_FIELDS = {
    "landmark_count", "heading_count", "heading_levels", "article_count", "aside_count",
    "section_count", "table_count", "list_count",
}
LANDMARK_FIELDS = {"tag", "role", "aria_label_present", "position", "overflow_y"}
INTERACTION_FIELDS = {
    "total_controls_sampled", "buttons", "internal_links", "external_links", "search_inputs",
    "disclosure_controls", "topic_disclosure_controls", "expanded_true",
    "controls_with_aria_controls", "topic_controls_with_aria_controls", "tablists", "tabs",
    "selected_tabs", "details", "back_to_top_controls",
}
ACCESSIBILITY_FIELDS = {
    "skip_links", "controls_with_accessible_metadata", "aria_live_regions", "aria_busy_regions",
    "current_markers",
}
CAPABILITY_ORDER = (
    "semantic_article",
    "complementary_regions",
    "live_content_status",
    "topic_tabs",
    "interactive_topic_tabs",
    "section_disclosure_state",
    "aria_control_relationships",
    "skip_navigation",
    "global_search",
    "fixed_global_header",
    "back_to_top",
    "table_surface",
    "current_markers",
)
DECISIONS = {"retain_current", "retain_shared", "consider", "no_signal"}


def _reject_unknown(value, allowed, path):
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {', '.join(sorted(unknown))}")


def _bounded_count(value, path, maximum=10000):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{path} must be an integer between 0 and {maximum}.")
    return value


def validate_structural_capture(capture):
    """Return a normalized copy only when capture contains structural, non-identifying fields."""
    _reject_unknown(
        capture,
        {
            "schema", "source_family", "viewport", "page", "landmarks", "interactions",
            "accessibility", "screenshot", "state_graph",
        },
        "capture",
    )
    if capture.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("capture.schema is unsupported.")
    if capture.get("source_family") not in SOURCE_FAMILIES:
        raise ValueError("capture.source_family is unsupported.")

    viewport = capture.get("viewport")
    _reject_unknown(viewport, {"width", "height"}, "capture.viewport")
    for key in ("width", "height"):
        value = _bounded_count(viewport.get(key), f"capture.viewport.{key}", maximum=10000)
        if value < 1:
            raise ValueError(f"capture.viewport.{key} must be positive.")

    page = capture.get("page")
    _reject_unknown(page, PAGE_FIELDS, "capture.page")
    for key in PAGE_FIELDS - {"heading_levels"}:
        _bounded_count(page.get(key), f"capture.page.{key}")
    heading_levels = page.get("heading_levels")
    if not isinstance(heading_levels, list) or len(heading_levels) > 256:
        raise ValueError("capture.page.heading_levels must be a bounded list.")
    if any(isinstance(level, bool) or not isinstance(level, int) or level not in range(1, 7) for level in heading_levels):
        raise ValueError("capture.page.heading_levels accepts only levels 1 through 6.")
    if len(heading_levels) != page.get("heading_count"):
        raise ValueError("capture.page.heading_count must match heading_levels.")

    landmarks = capture.get("landmarks")
    if not isinstance(landmarks, list) or len(landmarks) > 60:
        raise ValueError("capture.landmarks must be a bounded list.")
    for index, landmark in enumerate(landmarks):
        path = f"capture.landmarks[{index}]"
        _reject_unknown(landmark, LANDMARK_FIELDS, path)
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", str(landmark.get("tag", ""))):
            raise ValueError(f"{path}.tag is invalid.")
        if not re.fullmatch(r"[a-z-]{0,32}", str(landmark.get("role", ""))):
            raise ValueError(f"{path}.role is invalid.")
        if not isinstance(landmark.get("aria_label_present"), bool):
            raise ValueError(f"{path}.aria_label_present must be boolean.")
        if landmark.get("position") not in {"static", "relative", "absolute", "fixed", "sticky"}:
            raise ValueError(f"{path}.position is invalid.")
        if landmark.get("overflow_y") not in {"visible", "hidden", "auto", "scroll", "clip"}:
            raise ValueError(f"{path}.overflow_y is invalid.")

    interactions = capture.get("interactions")
    _reject_unknown(interactions, INTERACTION_FIELDS, "capture.interactions")
    for key in INTERACTION_FIELDS:
        _bounded_count(interactions.get(key), f"capture.interactions.{key}")

    accessibility = capture.get("accessibility")
    _reject_unknown(accessibility, ACCESSIBILITY_FIELDS, "capture.accessibility")
    for key in ACCESSIBILITY_FIELDS:
        _bounded_count(accessibility.get(key), f"capture.accessibility.{key}")

    screenshot = capture.get("screenshot")
    _reject_unknown(screenshot, {"sha256", "mime_type", "state"}, "capture.screenshot")
    if not re.fullmatch(r"[0-9a-f]{64}", str(screenshot.get("sha256", ""))):
        raise ValueError("capture.screenshot.sha256 must be a lowercase SHA-256 digest.")
    if screenshot.get("mime_type") not in {"image/png", "image/jpeg"}:
        raise ValueError("capture.screenshot.mime_type is unsupported.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", str(screenshot.get("state", ""))):
        raise ValueError("capture.screenshot.state is invalid.")

    state_graph = capture.get("state_graph")
    _reject_unknown(state_graph, {"states", "transitions"}, "capture.state_graph")
    states = state_graph.get("states")
    if not isinstance(states, list) or not 1 <= len(states) <= 16 or len(states) != len(set(states)):
        raise ValueError("capture.state_graph.states must contain 1 to 16 unique states.")
    if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", str(state)) for state in states):
        raise ValueError("capture.state_graph.states contains an invalid state.")
    transitions = state_graph.get("transitions")
    if not isinstance(transitions, list) or len(transitions) > 32:
        raise ValueError("capture.state_graph.transitions must be a bounded list.")
    allowed_actions = {"tab-select", "section-disclosure-toggle", "restore-reference-state"}
    for index, transition in enumerate(transitions):
        path = f"capture.state_graph.transitions[{index}]"
        _reject_unknown(transition, {"action", "from", "to", "verified"}, path)
        if transition.get("action") not in allowed_actions:
            raise ValueError(f"{path}.action is unsupported.")
        if transition.get("from") not in states or transition.get("to") not in states:
            raise ValueError(f"{path} must reference declared states.")
        if not isinstance(transition.get("verified"), bool):
            raise ValueError(f"{path}.verified must be boolean.")
    return json.loads(json.dumps(capture, sort_keys=True))


def _capabilities(capture):
    page = capture["page"]
    interactions = capture["interactions"]
    accessibility = capture["accessibility"]
    landmarks = capture["landmarks"]
    verified_actions = {
        item["action"] for item in capture["state_graph"]["transitions"] if item["verified"]
    }
    return {
        "semantic_article": page["article_count"] > 0,
        "complementary_regions": page["aside_count"] > 0,
        "live_content_status": accessibility["aria_live_regions"] > 0 or accessibility["aria_busy_regions"] > 0,
        "topic_tabs": interactions["tablists"] > 0 and interactions["tabs"] > 0,
        "interactive_topic_tabs": "tab-select" in verified_actions,
        "section_disclosure_state": "section-disclosure-toggle" in verified_actions,
        "aria_control_relationships": interactions["topic_controls_with_aria_controls"] > 0,
        "skip_navigation": accessibility["skip_links"] > 0,
        "global_search": interactions["search_inputs"] > 0,
        "fixed_global_header": any(
            item["tag"] == "header" and item["position"] in {"fixed", "sticky"} for item in landmarks
        ),
        "back_to_top": interactions["back_to_top_controls"] > 0,
        "table_surface": page["table_count"] > 0,
        "current_markers": accessibility["current_markers"] > 0,
    }


def build_reference_comparison(current_capture, reference_capture):
    """Compare capabilities without emitting source text, routes, geometry, or implementation changes."""
    current = validate_structural_capture(current_capture)
    reference = validate_structural_capture(reference_capture)
    if current["source_family"] != "llmebm-current":
        raise ValueError("current capture must use source_family llmebm-current.")
    if reference["source_family"] != "authenticated-medical-reference":
        raise ValueError("reference capture must use source_family authenticated-medical-reference.")
    current_capabilities = _capabilities(current)
    reference_capabilities = _capabilities(reference)
    decisions = []
    for capability in CAPABILITY_ORDER:
        has_current = current_capabilities[capability]
        has_reference = reference_capabilities[capability]
        if has_current and not has_reference:
            decision = "retain_current"
        elif has_current and has_reference:
            decision = "retain_shared"
        elif has_reference:
            decision = "consider"
        else:
            decision = "no_signal"
        decisions.append({
            "capability": capability,
            "current": has_current,
            "reference": has_reference,
            "decision": decision,
        })
    comparison = {
        "schema": COMPARISON_SCHEMA,
        "approval_required": True,
        "production_mutation": False,
        "slot_identity_policy": "preserve_existing_ids",
        "capture_evidence": {
            "current_screenshot_sha256": current["screenshot"]["sha256"],
            "reference_screenshot_sha256": reference["screenshot"]["sha256"],
        },
        "capability_decisions": decisions,
        "excluded_data_policy": ["content-and-identity", "visual-identity", "raw-page-material"],
        "implementation_gate": "human-review-before-any-template-or-sql-change",
    }
    canonical = json.dumps(comparison, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    comparison["comparison_hash"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return comparison


def validate_reference_comparison(comparison):
    """Reject tampered or expanded comparisons before a human decision becomes an import plan."""
    _reject_unknown(
        comparison,
        {
            "schema", "approval_required", "production_mutation", "slot_identity_policy",
            "capture_evidence", "capability_decisions", "excluded_data_policy",
            "implementation_gate", "comparison_hash",
        },
        "comparison",
    )
    if comparison.get("schema") != COMPARISON_SCHEMA:
        raise ValueError("comparison.schema is unsupported.")
    if comparison.get("approval_required") is not True:
        raise ValueError("comparison must require approval.")
    if comparison.get("production_mutation") is not False:
        raise ValueError("comparison must not describe a production mutation.")
    if comparison.get("slot_identity_policy") != "preserve_existing_ids":
        raise ValueError("comparison must preserve existing slot IDs.")

    evidence = comparison.get("capture_evidence")
    _reject_unknown(
        evidence,
        {"current_screenshot_sha256", "reference_screenshot_sha256"},
        "comparison.capture_evidence",
    )
    for key in ("current_screenshot_sha256", "reference_screenshot_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get(key, ""))):
            raise ValueError(f"comparison.capture_evidence.{key} is invalid.")

    decisions = comparison.get("capability_decisions")
    if not isinstance(decisions, list) or len(decisions) != len(CAPABILITY_ORDER):
        raise ValueError("comparison.capability_decisions must cover every capability once.")
    expected_decision = {
        (True, False): "retain_current",
        (True, True): "retain_shared",
        (False, True): "consider",
        (False, False): "no_signal",
    }
    for index, (item, capability) in enumerate(zip(decisions, CAPABILITY_ORDER)):
        path = f"comparison.capability_decisions[{index}]"
        _reject_unknown(item, {"capability", "current", "reference", "decision"}, path)
        if item.get("capability") != capability:
            raise ValueError("comparison capabilities are missing, duplicated, or out of order.")
        if not isinstance(item.get("current"), bool) or not isinstance(item.get("reference"), bool):
            raise ValueError(f"{path} current/reference flags must be boolean.")
        if item.get("decision") not in DECISIONS:
            raise ValueError(f"{path}.decision is unsupported.")
        if item["decision"] != expected_decision[(item["current"], item["reference"])]:
            raise ValueError(f"{path}.decision does not match its capability flags.")

    if comparison.get("excluded_data_policy") != [
        "content-and-identity", "visual-identity", "raw-page-material"
    ]:
        raise ValueError("comparison.excluded_data_policy is unsupported.")
    if comparison.get("implementation_gate") != "human-review-before-any-template-or-sql-change":
        raise ValueError("comparison.implementation_gate is unsupported.")
    supplied_hash = str(comparison.get("comparison_hash", ""))
    payload = {key: value for key, value in comparison.items() if key != "comparison_hash"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if supplied_hash != expected_hash:
        raise ValueError("comparison hash does not match its payload.")
    return json.loads(json.dumps(comparison, sort_keys=True))


def build_reference_import_plan(comparison, approved_capabilities):
    """Build the smallest reviewed plan: generic capabilities only, with zero hierarchy import."""
    normalized = validate_reference_comparison(comparison)
    if not isinstance(approved_capabilities, (list, tuple, set)):
        raise ValueError("approved_capabilities must be a bounded collection.")
    if len(approved_capabilities) > len(CAPABILITY_ORDER) or any(
        not isinstance(item, str) for item in approved_capabilities
    ):
        raise ValueError("approved_capabilities contains invalid values.")
    approved_set = set(approved_capabilities)
    if not approved_set or len(approved_set) != len(approved_capabilities):
        raise ValueError("approved_capabilities must contain unique reviewed capabilities.")
    reviewable = {
        item["capability"]
        for item in normalized["capability_decisions"]
        if item["decision"] == "consider"
    }
    unknown = approved_set - reviewable
    if unknown:
        raise ValueError(
            "approved_capabilities are not reviewable candidates: " + ", ".join(sorted(unknown))
        )

    approved = [capability for capability in CAPABILITY_ORDER if capability in approved_set]
    retained = [
        item["capability"]
        for item in normalized["capability_decisions"]
        if item["decision"] in {"retain_current", "retain_shared"}
    ]
    deferred = [
        item["capability"]
        for item in normalized["capability_decisions"]
        if item["decision"] == "consider" and item["capability"] not in approved_set
    ]
    plan = {
        "schema": IMPORT_PLAN_SCHEMA,
        "review_status": "approved",
        "import_mode": "capabilities-only",
        "comparison_hash": normalized["comparison_hash"],
        "approved_capabilities": approved,
        "retained_capabilities": retained,
        "deferred_capabilities": deferred,
        "hierarchy_changes": [],
        "slot_identity_policy": "preserve_existing_ids",
        "external_data_retained": [],
        "runtime_dependency": False,
    }
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    plan["plan_hash"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return plan


def _load_capture(path):
    path = Path(path)
    if path.stat().st_size > 256 * 1024:
        raise ValueError(f"Capture exceeds 256 KiB: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Compare sanitized llmebm and reference layout capabilities.")
    parser.add_argument("--current-capture", required=True)
    parser.add_argument("--reference-capture", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    comparison = build_reference_comparison(
        _load_capture(args.current_capture), _load_capture(args.reference_capture)
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "candidate_only",
        "comparison_hash": comparison["comparison_hash"],
        "output": str(output.resolve()),
        "production_mutation": False,
    }))


if __name__ == "__main__":
    main()
