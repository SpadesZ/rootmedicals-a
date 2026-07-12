# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core5_api/schemas.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core5 API 層，對外提供 health/check/admin 等服務端路由。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
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
