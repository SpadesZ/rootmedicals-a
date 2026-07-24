# 模組定位: ebm-rag 文獻使用權與來源生命週期的 fail-closed policy gate。
# 主要責任: 驗證 paper-level policy registry，並判定指定用途是否允許使用來源。
# 呼叫來源: corpus prepare、medical publish gate、source withdrawal/refresh 與 contract tests。
# 輸入契約: rootmedicals-source-policy.v1 JSON；paper_id、license/lifecycle/use 欄位完整。
# 輸出契約: 正規化 paper policy map 與逐來源 blocked reasons；不做法律推測。
# 安全邊界: 缺 policy、非 current、未核准用途或商用旗標不明一律拒絕。
# 維護提醒: 權利狀態只能依正式許可/法律審查更新，不能由 LLM 或網頁可讀性推定。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


POLICY_SCHEMA = "rootmedicals-source-policy.v1"
REGISTRY_SCHEMA = "rootmedicals-source-policy-registry.v1"
LICENSE_STATUSES = {"approved", "permission_required", "review_required", "prohibited"}
LIFECYCLE_STATUSES = {"current", "superseded", "withdrawn", "expired"}
ALLOWED_USES = {"internal_validation", "retrieval_generation", "commercial_publication"}
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("atrial_fibrillation_source_policies.v1.json")
PUBLIC_SOURCE_DETAIL_SCHEMA = "rootmedicals-source-details.v1"


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def validate_source_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("source policy must be an object")
    required = {
        "schema", "paper_id", "document_version", "lifecycle_status", "license_status",
        "permitted_uses", "commercial_publication_allowed", "rights_source_url", "rights_reviewed_at",
    }
    missing = required.difference(value)
    if missing:
        raise ValueError(f"source policy is missing: {', '.join(sorted(missing))}")
    if value.get("schema") != POLICY_SCHEMA:
        raise ValueError(f"source policy schema must be {POLICY_SCHEMA}")
    lifecycle_status = _required_text(value.get("lifecycle_status"), "lifecycle_status")
    license_status = _required_text(value.get("license_status"), "license_status")
    if lifecycle_status not in LIFECYCLE_STATUSES:
        raise ValueError("source policy lifecycle_status is invalid")
    if license_status not in LICENSE_STATUSES:
        raise ValueError("source policy license_status is invalid")
    permitted_uses = value.get("permitted_uses")
    if not isinstance(permitted_uses, list) or len(set(permitted_uses)) != len(permitted_uses):
        raise ValueError("source policy permitted_uses must be a unique list")
    if not set(permitted_uses).issubset(ALLOWED_USES):
        raise ValueError("source policy permitted_uses contains an unknown use")
    commercial_allowed = value.get("commercial_publication_allowed")
    if not isinstance(commercial_allowed, bool):
        raise ValueError("source policy commercial_publication_allowed must be boolean")
    normalized = dict(value)
    normalized.update({
        "schema": POLICY_SCHEMA,
        "paper_id": _required_text(value.get("paper_id"), "paper_id"),
        "document_version": _required_text(value.get("document_version"), "document_version"),
        "lifecycle_status": lifecycle_status,
        "license_status": license_status,
        "permitted_uses": list(permitted_uses),
        "commercial_publication_allowed": commercial_allowed,
        "rights_source_url": _required_text(value.get("rights_source_url"), "rights_source_url"),
        "rights_reviewed_at": _required_text(value.get("rights_reviewed_at"), "rights_reviewed_at"),
    })
    return normalized


def load_source_policy_registry(path: Path | None = None) -> dict[str, dict[str, Any]]:
    registry_path = path or DEFAULT_REGISTRY_PATH
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != REGISTRY_SCHEMA:
        raise ValueError(f"source policy registry schema must be {REGISTRY_SCHEMA}")
    shared_sources = data.get("sources")
    if not isinstance(shared_sources, list) or not shared_sources:
        raise ValueError("source policy registry sources must be a non-empty list")
    policies: dict[str, dict[str, Any]] = {}
    for source in shared_sources:
        if not isinstance(source, dict):
            raise ValueError("source policy registry entry must be an object")
        paper_ids = source.get("paper_ids")
        if not isinstance(paper_ids, list) or not paper_ids:
            raise ValueError("source policy registry entry paper_ids must be non-empty")
        base = {key: item for key, item in source.items() if key != "paper_ids"}
        for paper_id in paper_ids:
            policy = validate_source_policy({**base, "schema": POLICY_SCHEMA, "paper_id": paper_id})
            if policy["paper_id"] in policies:
                raise ValueError(f"duplicate source policy paper_id: {policy['paper_id']}")
            policies[policy["paper_id"]] = policy
    return policies


def evaluate_source_use(
    policies: dict[str, dict[str, Any]], paper_ids: list[str], required_use: str,
) -> dict[str, Any]:
    if required_use not in ALLOWED_USES:
        raise ValueError("required_use is invalid")
    normalized_ids = list(dict.fromkeys(str(item or "").strip() for item in paper_ids))
    if not normalized_ids or any(not item for item in normalized_ids):
        raise ValueError("paper_ids must be a non-empty list")
    blocked = []
    for paper_id in normalized_ids:
        raw_policy = policies.get(paper_id)
        if raw_policy is None:
            blocked.append({"paper_id": paper_id, "reason": "source_policy_missing"})
            continue
        policy = validate_source_policy(raw_policy)
        if policy["lifecycle_status"] != "current":
            blocked.append({"paper_id": paper_id, "reason": f"source_{policy['lifecycle_status']}"})
        elif policy["license_status"] != "approved":
            blocked.append({"paper_id": paper_id, "reason": f"license_{policy['license_status']}"})
        elif required_use not in policy["permitted_uses"]:
            blocked.append({"paper_id": paper_id, "reason": "use_not_permitted"})
        elif required_use == "commercial_publication" and not policy["commercial_publication_allowed"]:
            blocked.append({"paper_id": paper_id, "reason": "commercial_publication_not_allowed"})
    return {
        "allowed": not blocked,
        "required_use": required_use,
        "paper_ids": normalized_ids,
        "blocked": blocked,
    }


def build_public_source_details(
    metadata_by_paper: dict[str, dict[str, Any]], paper_ids: list[str],
) -> dict[str, Any]:
    """Project reviewed metadata into a public, text-only shape without local paths or source text."""
    normalized_ids = list(dict.fromkeys(str(item or "").strip() for item in paper_ids))
    if not normalized_ids or any(not item for item in normalized_ids):
        raise ValueError("paper_ids must be a non-empty list")
    sources = []
    missing = []
    for paper_id in normalized_ids:
        metadata = metadata_by_paper.get(paper_id)
        if not isinstance(metadata, dict):
            missing.append(paper_id)
            continue
        raw_policy = metadata.get("source_policy")
        try:
            policy = validate_source_policy(raw_policy) if isinstance(raw_policy, dict) else None
        except ValueError:
            policy = None
        # ponytail: only this explicit projection is public; add a field here only after provenance review.
        sources.append({
            "paper_id": paper_id,
            "title": str(metadata.get("guideline_title") or metadata.get("citation_text") or paper_id),
            "organization": str(
                metadata.get("guideline_organization")
                or (policy or {}).get("organization")
                or ""
            ),
            "publication_year": metadata.get("publication_year") or (policy or {}).get("publication_year"),
            "doi": str(metadata.get("doi") or (policy or {}).get("doi") or ""),
            "pmid": str(metadata.get("pmid") or ""),
            "journal": str(metadata.get("journal") or ""),
            "source_type": str(metadata.get("source_type") or ""),
            "source_url": str(
                metadata.get("source_url")
                or metadata.get("guideline_url")
                or (policy or {}).get("source_url")
                or ""
            ),
            "document_version": str((policy or {}).get("document_version") or ""),
            "lifecycle_status": str((policy or {}).get("lifecycle_status") or "unknown"),
            "license_status": str((policy or {}).get("license_status") or "unknown"),
            "commercial_publication_allowed": bool(
                (policy or {}).get("commercial_publication_allowed", False)
            ),
            "rights_source_url": str((policy or {}).get("rights_source_url") or ""),
        })
    return {
        "schema": PUBLIC_SOURCE_DETAIL_SCHEMA,
        "sources": sources,
        "missing_paper_ids": missing,
    }
