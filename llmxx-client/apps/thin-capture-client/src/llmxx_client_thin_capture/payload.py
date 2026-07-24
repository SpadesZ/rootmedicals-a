# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/payload.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.4-payload 契約註解
# 模組定位:
#   thin client 的對外 payload 組裝點。這裡決定醫師端送到 llmxx-server /api/intake 的 JSON 形狀。
# 主要責任:
#   1. 建立 llmxx-client-screenshot.v0.1 payload，包含截圖、視窗尺寸、layout hints。
#   2. 帶入 ClinicalGuard sidecar 的 ICD 與目前 SOAP/vitals context；client 不做 OCR，只轉送結構化欄位。
#   3. 產生本機 diagnostics 可保存的遮蔽副本。
# 輸入契約:
#   image_b64 是記憶體 PNG；layout_regions 是座標提示；clinical_metadata 可包含 ICD 與 mock HIS 當前欄位。
# 輸出契約:
#   回傳 dict，由 ServerClient 直接 POST；本檔不負責 HTTP、OCR 或 final gate。
# 安全邊界:
#   patient_uid 固定遮蔽；diagnostics 一律遮蔽 screenshot.image_b64。
#   若要新增欄位，先確認它是否含 PHI，以及 server contracts.schemas 是否允許。
# 維護提醒:
#   schema_version 變更會影響 server _parse_payload()，不可只改 client。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from .models import WindowInfo


ALLERGIC_RHINITIS_FIXTURE_ID = "allergic_rhinitis_intranasal_steroid"
AF_ACTIVE_BLEEDING_FIXTURE_ID = "atrial_fibrillation_active_bleeding"


def _select_demo_fixture_id(configured_id: Any, clinical_metadata: Dict[str, Any]) -> str:
    configured = str(configured_id or "").strip()
    if configured and configured != ALLERGIC_RHINITIS_FIXTURE_ID:
        return configured

    soap = clinical_metadata.get("soap") if isinstance(clinical_metadata.get("soap"), dict) else {}
    clinical_text = " ".join(
        str(value or "")
        for value in (
            clinical_metadata.get("normalized_diagnosis"),
            clinical_metadata.get("dx_text"),
            soap.get("S"),
            soap.get("O"),
            soap.get("A"),
            soap.get("P"),
        )
    ).lower()
    has_af = "atrial fibrillation" in clinical_text or "afib" in clinical_text
    has_active_bleeding = any(term in clinical_text for term in ("active bleeding", "active gastrointestinal bleeding", "major bleeding"))
    has_anticoagulation = any(term in clinical_text for term in ("anticoagulation", "anticoagulant", "apixaban"))
    # ponytail: Demo-only matcher stays deliberately narrow; add an explicit scenario marker if this grows beyond two fixtures.
    if has_af and has_active_bleeding and has_anticoagulation:
        return AF_ACTIVE_BLEEDING_FIXTURE_ID
    return configured or ALLERGIC_RHINITIS_FIXTURE_ID


def build_screenshot_payload(
    *,
    config: Dict[str, Any],
    window: WindowInfo,
    image_b64: str,
    image_width: int,
    image_height: int,
    layout_regions: List[Dict[str, Any]] | None = None,
    clinical_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """建立薄客戶端 payload：只送截圖與欄位提示，不在 client 端產生 OCR 文字。"""
    # session_id 用於醫師端 polling；server 另有自己的 server session id。
    # 兩者都保留，可以追「同一次 Ctrl+Alt+G」從 client 到 server 的完整鏈路。
    now = datetime.now(timezone.utc).isoformat()
    session_id = f"llmxx-{uuid.uuid4().hex[:12]}"
    payload: Dict[str, Any] = {
        "schema_version": "llmxx-client-screenshot.v0.1",
        "session_id": session_id,
        "created_at": now,
        "source": "llmxx-client-thin-screenshot",
        "input_origin": "clinical_guard_standalone",
        "zero_disk_image_io": bool(config.get("app", {}).get("zero_disk_image_io", True)),
        "patient_uid": "[REDACTED]",
        "window": {
            "title": window.title,
            "width": int(image_width),
            "height": int(image_height),
        },
        "screenshot": {
            "format": "png",
            "image_b64": image_b64,
            "width": int(image_width),
            "height": int(image_height),
        },
        "layout_regions": list(layout_regions or []),
    }
    # ICD 是醫師端已選擇的結構化資訊，優先由 sidecar 帶入，避免 ICD 也被截圖 OCR 誤讀。
    safe_clinical_metadata = _sanitize_clinical_metadata(clinical_metadata)
    if safe_clinical_metadata:
        payload["clinical_metadata"] = safe_clinical_metadata
    demo_config = config.get("demo_fixture", {}) if isinstance(config.get("demo_fixture"), dict) else {}
    env_enabled = str(os.environ.get("LLMXX_CLIENT_DEMO_FIXTURE_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    if bool(demo_config.get("enabled", False)) or env_enabled:
        # Demo Fixture marker 必須由控制入口或設定明確打開；Live + Synthetic 不帶這兩個欄位。
        payload["demo_mode"] = str(demo_config.get("mode") or "demo_fixture")
        payload["demo_fixture_id"] = _select_demo_fixture_id(demo_config.get("fixture_id"), safe_clinical_metadata)
    return payload


def _sanitize_clinical_metadata(clinical_metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    # 維護筆記:
    # sidecar 仍然禁止 patient label、visit time 與其他識別欄位；但 SOAP/A/P 是醫師端
    # 畫面已存在的 clinical context。這些欄位讓 server 可避免 OCR 把處置讀爛而誤降級。
    allowed_keys = ("icd_code", "icd10_code", "diagnosis_label", "normalized_diagnosis", "metadata_source", "dx_text")
    if not isinstance(clinical_metadata, dict):
        return {}
    safe: Dict[str, Any] = {}
    for key in allowed_keys:
        value = str(clinical_metadata.get(key) or "").strip()
        if value:
            safe[key] = value[:240]
    soap = clinical_metadata.get("soap")
    if isinstance(soap, dict):
        safe_soap: Dict[str, str] = {}
        for key in ("S", "O", "A", "P"):
            value = str(soap.get(key) or "").strip()
            if value:
                safe_soap[key] = value[:1200]
        if safe_soap:
            safe["soap"] = safe_soap
    vitals = clinical_metadata.get("vital_signs")
    if isinstance(vitals, dict):
        safe_vitals: Dict[str, str] = {}
        for key in ("bp", "hr", "temp", "rr", "spo2"):
            value = str(vitals.get(key) or "").strip()
            if value:
                safe_vitals[key] = value[:40]
        if safe_vitals:
            safe["vital_signs"] = safe_vitals
    return safe


def redact_screenshot_payload_for_local_logs(payload: Dict[str, Any]) -> Dict[str, Any]:
    """回傳可落地的診斷副本；截圖 base64 一律遮蔽，避免病歷畫面被寫入 log。"""
    safe = dict(payload or {})
    screenshot = safe.get("screenshot")
    if isinstance(screenshot, dict):
        redacted = dict(screenshot)
        if "image_b64" in redacted:
            redacted["image_b64"] = "[REDACTED_SCREENSHOT_BASE64]"
        safe["screenshot"] = redacted
    metadata = safe.get("clinical_metadata")
    if isinstance(metadata, dict):
        metadata_safe = dict(metadata)
        if isinstance(metadata_safe.get("soap"), dict):
            metadata_safe["soap"] = {key: "[REDACTED_STRUCTURED_CLINICAL_TEXT]" for key in metadata_safe["soap"].keys()}
        if isinstance(metadata_safe.get("vital_signs"), dict):
            metadata_safe["vital_signs"] = {key: "[REDACTED_VITAL_SIGN]" for key in metadata_safe["vital_signs"].keys()}
        safe["clinical_metadata"] = metadata_safe
    return safe
