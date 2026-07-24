# 模組定位: ebm-rag Core5 legacy 與 Topic API 的 Pydantic trust-boundary schemas。
# 主要責任: 驗證 query/check、Topic manifest/screenshots、slot filters、scoped revision 與 mapping review 邊界。
# 呼叫來源: rag_core/core5_api/router.py 的 public/internal endpoints。
# 輸入契約: 未可信 JSON；Topic screenshot 必須是 PNG/JPEG base64 且數量/大小受限。
# 輸出契約: 只產生 retrieval/orchestrator 可安全消費的 typed request models。
# 安全邊界: unknown/oversized image 與非法 manifest 欄位在 provider 呼叫前拒絕。
# 維護提醒: 保留 /query 與 /check 相容欄位；新增 Topic 欄位不可改壞 legacy callers。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core5_api/schemas.py
# Timestamp: 2026-06-17 00:00 +08:00
# Version: v0.5
# Description: Core5 API 請求 Schema。QueryRequest 供 /query 使用；CheckRequest 供 /check（llmxx 相容入口）使用。
# Change Notes:
#   - v0.2: Allow /check callers to pass safe retrieval filters, including the
#     explicit demo_synthetic_fallback verifier-gated option.
#   - v0.3: Accept ICD-10 code as a disease-classification anchor for the
#     conservative traffic-light gate.
#   - v0.4: Accept icd10_code, diagnosis_label, and dx_text so /check can
#     prioritize ICD-normalized diagnosis over OCR-only assessment text.
#   - v0.5: Accept normalized_diagnosis separately from display label.
# ----------------------------------------------------------------------------------------------------
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional

from lava.adapter.base import BaseLavaAdapter
from rag_core.common.evidence_scope import canonical_topic_key, slot_key_from_id
from rag_core.core1_ingestion.source_policy import ALLOWED_USES

class QueryRequest(BaseModel):
    dx_summary: str
    case_context: Optional[dict] = None
    filters: Optional[dict] = None
    top_k: int = 10

class CheckRequest(BaseModel):
    dx: str
    tx: Optional[str] = None
    hx: Optional[str] = None
    icd_code: Optional[str] = None
    icd10_code: Optional[str] = None
    diagnosis_label: Optional[str] = None
    normalized_diagnosis: Optional[str] = None
    dx_text: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    labs: Optional[dict] = None
    filters: Optional[dict] = None
    top_k: int = 10


class TopicScreenshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mime_type: str
    image_b64: str = Field(min_length=1, max_length=7_100_000)
    state: str = Field(default="", max_length=120)

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, value: str) -> str:
        mime_type = str(value or "").strip().lower()
        if mime_type not in {"image/png", "image/jpeg"}:
            raise ValueError("mime_type must be image/png or image/jpeg")
        return mime_type

    @field_validator("image_b64")
    @classmethod
    def validate_image_b64(cls, value: str, info) -> str:
        # MIME is validated again with the decoded payload in the task boundary.
        mime_type = info.data.get("mime_type", "")
        BaseLavaAdapter.validate_vision_images([{"mime_type": mime_type, "image_b64": value}])
        return value


class TopicContentGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict
    screenshots: list[TopicScreenshotRequest] = Field(min_length=1, max_length=3)
    only_slot_ids: list[str] = Field(default_factory=list, max_length=200)
    filters: dict = Field(default_factory=dict)
    top_k: int = Field(default=10, ge=1, le=50)

    @field_validator("manifest")
    @classmethod
    def validate_manifest_size(cls, value: dict) -> dict:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 1024 * 1024:
            raise ValueError("manifest exceeds 1 MiB")
        return value
    @field_validator("only_slot_ids")
    @classmethod
    def validate_only_slot_ids(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            slot_id = str(item or "").strip()
            if not slot_id or len(slot_id) > 300:
                raise ValueError("only_slot_ids entries must be non-empty and at most 300 characters")
            normalized.append(slot_id)
        if len(set(normalized)) != len(normalized):
            raise ValueError("only_slot_ids contains duplicates")
        return normalized

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, value: dict) -> dict:
        allowed = {
            "specialty", "disease", "source_type", "is_guideline", "has_contraindication_terms",
            "min_ocebm", "min_ocebm_level", "prefer_six_s_levels", "query_decomposition_mode"
        }
        extra = set(value).difference(allowed)
        if extra:
            raise ValueError(f"filters contains unknown fields: {', '.join(sorted(extra))}")
        for key, item in value.items():
            if key in {"is_guideline", "has_contraindication_terms"}:
                if not isinstance(item, bool):
                    raise ValueError(f"filters.{key} must be a boolean")
            elif key == "prefer_six_s_levels":
                if not isinstance(item, list) or not 1 <= len(item) <= 5:
                    raise ValueError("filters.prefer_six_s_levels must contain 1 to 5 strings")
                if any(not isinstance(part, str) or not part.strip() or len(part) > 40 for part in item):
                    raise ValueError("filters.prefer_six_s_levels entries must be strings up to 40 characters")
            elif not isinstance(item, str) or not item.strip() or len(item) > 240:
                raise ValueError(f"filters.{key} must be a non-empty string up to 240 characters")
        if len(json.dumps(value, ensure_ascii=False)) > 20_000:
            raise ValueError("filters payload is too large")
        return value


class TopicEvidenceRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_key: str = Field(min_length=1, max_length=160)
    slot_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("topic_key")
    @classmethod
    def validate_topic_key(cls, value: str) -> str:
        normalized = canonical_topic_key(value)
        if normalized == "*":
            raise ValueError("topic_key must identify a specific topic")
        return normalized

    @field_validator("slot_ids")
    @classmethod
    def validate_slot_ids(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip() for item in value]
        if any(len(item) > 300 or slot_key_from_id(item) == "*" for item in normalized):
            raise ValueError("slot_ids entries must be valid universal/custom slot IDs up to 300 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("slot_ids contains duplicates")
        return normalized


class TopicScopeReviewStatusRequest(TopicEvidenceRevisionRequest):
    """Bounded read-only lookup for append-only evidence mapping reviews."""


class TopicScopeReviewApproveRequest(BaseModel):
    """Approve one exact current source-to-slot mapping; source IDs are resolved server-side."""

    model_config = ConfigDict(extra="forbid")

    topic_key: str = Field(min_length=1, max_length=160)
    slot_id: str = Field(min_length=1, max_length=300)
    reviewed_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("topic_key")
    @classmethod
    def validate_topic_key(cls, value: str) -> str:
        normalized = canonical_topic_key(value)
        if normalized == "*":
            raise ValueError("topic_key must identify a specific topic")
        return normalized

    @field_validator("slot_id")
    @classmethod
    def validate_slot_id(cls, value: str) -> str:
        normalized = slot_key_from_id(value)
        if normalized == "*":
            raise ValueError("slot_id must identify one universal/custom slot")
        return normalized

    @field_validator("reviewed_by", "reason")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("reviewed_by and reason must not be blank")
        return normalized


class SourceUseGateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_ids: list[str] = Field(min_length=1, max_length=200)
    required_use: str

    @field_validator("paper_ids")
    @classmethod
    def validate_paper_ids(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("paper_ids entries must be non-empty and at most 200 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("paper_ids contains duplicates")
        return normalized

    @field_validator("required_use")
    @classmethod
    def validate_required_use(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in ALLOWED_USES:
            raise ValueError("required_use is invalid")
        return normalized


class SourceDetailsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("paper_ids")
    @classmethod
    def validate_paper_ids(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("paper_ids entries must be non-empty and at most 200 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("paper_ids contains duplicates")
        return normalized
