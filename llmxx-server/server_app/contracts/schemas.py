# 檔案路徑: rootmedicals-a/llmxx-server/server_app/contracts/schemas.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.3-schema 契約註解
# 模組定位:
#   llmxx-server 的 payload contract 集中處。API route、server OCR、clinical mapper、
#   final gate 都引用這些 Pydantic model，避免各層自行猜欄位。
# 主要責任:
#   1. 定義 thin screenshot payload、formal clinical payload、encrypted envelope 與 intake response。
#   2. 用 extra="forbid" 擋掉 diagnostics wrapper、raw image wrapper 或未知欄位混入正式臨床契約。
#   3. 讓新舊 client schema 在 server 端有明確分界。
# 呼叫來源:
#   api.main、ocr.server_ocr、core.clinical_mapper、core.response_builder。
# 安全邊界:
#   model 只描述資料形狀，不做臨床決策；任何燈號、安全降級都應留在 core 層。
# 維護提醒:
#   新增欄位時要確認來源、是否含 PHI、是否會被寫入 state_db，以及 doctor alert 是否需要顯示。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    # 外部 payload 一律拒絕未知欄位。這比寬鬆接收麻煩一點，
    # 但能避免 diagnostics wrapper 或 raw screenshot archive 混進正式 clinical contract。
    model_config = ConfigDict(extra="forbid")


class SoapPayload(StrictModel):
    S: str = ""
    O: str = ""
    A: str = ""
    P: str = ""


class VitalSignsPayload(StrictModel):
    bp: str = ""
    hr: str = ""
    temp: str = ""
    rr: str = ""
    spo2: str = ""


class FormalClientPayload(StrictModel):
    # Formal payload 是 server 後續核心流程唯一吃的病歷契約。
    # 即使來源是 screenshot，server_ocr 也會先轉成這個形狀再進 clinical_mapper。
    schema_version: str
    session_id: str = ""
    created_at: str = ""
    source: str = ""
    input_origin: str = ""
    zero_disk_image_io: bool = True
    patient_uid: str = "[REDACTED]"
    soap: SoapPayload
    vital_signs: VitalSignsPayload = Field(default_factory=VitalSignsPayload)
    icd_code: str = ""
    icd10_code: str = ""
    diagnosis_label: str = ""
    normalized_diagnosis: str = ""
    dx_text: str = ""
    clinical_text: str = ""
    redactions: dict[str, Any] = Field(default_factory=dict)
    normalizations: dict[str, Any] = Field(default_factory=dict)
    demo_mode: str = ""
    demo_fixture_id: str = ""


class ScreenshotImagePayload(StrictModel):
    format: str = "png"
    image_b64: str
    width: int = 0
    height: int = 0


class ScreenshotWindowPayload(StrictModel):
    title: str = ""
    width: int = 0
    height: int = 0


class ScreenshotLayoutRegionPayload(StrictModel):
    field_name: str
    x: int
    y: int
    w: int
    h: int
    method: str = ""


class ScreenshotClinicalMetadataPayload(StrictModel):
    icd_code: str = ""
    icd10_code: str = ""
    diagnosis_label: str = ""
    normalized_diagnosis: str = ""
    dx_text: str = ""
    soap: SoapPayload = Field(default_factory=SoapPayload)
    vital_signs: VitalSignsPayload = Field(default_factory=VitalSignsPayload)
    metadata_source: str = ""


class ScreenshotClientPayload(StrictModel):
    # Thin client 的真正外部輸入：截圖 + layout hints + 非 PHI metadata。
    # 這個 schema 不承載 OCR 文字，OCR 權責集中在 llmxx-server。
    schema_version: str
    session_id: str = ""
    created_at: str = ""
    source: str = ""
    input_origin: str = ""
    zero_disk_image_io: bool = True
    patient_uid: str = "[REDACTED]"
    window: ScreenshotWindowPayload = Field(default_factory=ScreenshotWindowPayload)
    screenshot: ScreenshotImagePayload
    layout_regions: list[ScreenshotLayoutRegionPayload] = Field(default_factory=list)
    clinical_metadata: ScreenshotClinicalMetadataPayload = Field(default_factory=ScreenshotClinicalMetadataPayload)
    demo_mode: str = ""
    demo_fixture_id: str = ""


class EncryptedEnvelope(StrictModel):
    schema_version: str
    algorithm: str
    nonce_b64: str
    ciphertext_b64: str
    key_id: Optional[str] = None


class ClinicalParse(StrictModel):
    # ClinicalParse 是 server 整理後的臨床語意層，供 RAG、LAVA、final gate 共用。
    # dx_text 保留 A 欄原文；dx/normalized_diagnosis 則可被 deterministic 或 LAVA parse 補強。
    dx: str = ""
    tx: str = ""
    hx: str = ""
    icd_code: str = ""
    icd10_code: str = ""
    icd_label: str = ""
    diagnosis_label: str = ""
    normalized_diagnosis: str = ""
    dx_text: str = ""
    age: Optional[int] = None
    sex: Optional[str] = None
    labs: dict[str, Any] = Field(default_factory=dict)
    parse_confidence: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)
    llm_status: str = "fallback"
    trace: dict[str, str] = Field(default_factory=dict)


class ErrorEnvelope(StrictModel):
    ok: bool = False
    status: str = "failed"
    error_code: str
    retryable: bool = False
    message: str
    session_id: Optional[str] = None


class IntakeResponse(StrictModel):
    # IntakeResponse 是醫師端浮窗、工程 viewer、session polling 的共同回應形狀。
    # 這裡刻意用 dict 裝 ebm/final_gate，是為了容納 RAG 演進中的 metadata；安全檢查在 response_builder。
    ok: bool
    status: str
    session_id: str
    client_session_id: str = ""
    correlation_id: str
    error_code: Optional[str] = None
    retryable: bool = False
    clinical_parse: dict[str, Any] = Field(default_factory=dict)
    ebm: dict[str, Any] = Field(default_factory=dict)
    adjudication: dict[str, Any] = Field(default_factory=dict)
    claim_verify: dict[str, Any] = Field(default_factory=dict)
    demo_verifier: dict[str, Any] = Field(default_factory=dict)
    final_gate: dict[str, Any] = Field(default_factory=dict)
    events_url: str = ""

