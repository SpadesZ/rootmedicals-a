# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/pipeline.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.3-thin client 交接註解
# 模組定位:
#   醫師端 Ctrl+Alt+G 的主流程。這支檔案只負責找 HIS 視窗、截圖、建立 layout hints、
#   讀取 ClinicalGuard ICD sidecar、POST 給 llmxx-server，然後寫遮蔽後 diagnostics。
# 主要責任:
#   1. 保持 client 薄化：不做 OCR、不做 medical normalization、不產 formal clinical payload。
#   2. 提供 server OCR 可用的 SOAP/Vital/ICD 區域提示，降低 server 端影像偵測不穩定。
#   3. 本機 diagnostics 僅保存遮蔽後 payload，避免截圖 base64 留在磁碟。
# 呼叫來源:
#   CLI run-once、hotkey runner、system tray 都呼叫 ThinCapturePipeline.run_once()。
# 輸入契約:
#   需要可見的 ClinicalGuard mock HIS 視窗，以及 config/default_config.json 的 capture/network 設定。
# 輸出契約:
#   PipelineResult 內含送出的 screenshot payload、server response、diagnostics 路徑；server_payload_path 固定為 None。
# 安全邊界:
#   如果要調 OCR 或正規化，請改 llmxx-server/ocr/server_ocr.py，不要把 OCR 引擎加回 client。
# 維護提醒:
#   修改此檔後至少跑：CLI --help、fill-mock-his、run-once --send、Ctrl+Alt+G 手動觸發。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

from .capture import SoapFieldCapture
from .models import CaptureRegion, PipelineResult
from .payload import build_screenshot_payload, redact_screenshot_payload_for_local_logs
from .server_client import ServerClient
from .verification import write_diagnostics
from .window_locator import WindowLocator, enable_dpi_awareness


class ThinCapturePipeline:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.locator = WindowLocator(config.get("target_window", {}))
        self.capture = SoapFieldCapture(config.get("capture", {}))
        self.server = ServerClient(config.get("network", {}))

    def run_once(self, *, send_override: Optional[bool] = None, expected_path: Optional[str] = None) -> PipelineResult:
        # run_once 是所有觸發方式的共同入口；hotkey/tray/CLI 都走這裡。
        # expected_path 是舊驗證腳本留下的參數，thin client 不再用它讀取預期 OCR 文字。
        enable_dpi_awareness()
        if send_override is not None:
            self.config.setdefault("network", {})["send_enabled"] = bool(send_override)
        _ = expected_path

        window = self.locator.find_target_window()
        return self._run_thin_screenshot_once(window)

    def _run_thin_screenshot_once(self, window: Any) -> PipelineResult:
        # 這段刻意先把截圖轉成記憶體 PNG，再立即組 payload 送出。
        # 除 diagnostics 的遮蔽 JSON 外，不在本機落地任何原始截圖或 OCR 文字。
        window_image = self.capture.capture_window_image(window)
        image_b64 = self._encode_png_base64(window_image)
        height, width = window_image.shape[:2]
        layout_regions = self._build_layout_hints(window=window, window_image=window_image)
        clinical_metadata = self._read_clinical_metadata_sidecar()
        payload = build_screenshot_payload(
            config=self.config,
            window=window,
            image_b64=image_b64,
            image_width=width,
            image_height=height,
            layout_regions=layout_regions,
            clinical_metadata=clinical_metadata,
        )
        safe_payload = redact_screenshot_payload_for_local_logs(payload)

        # server_response 可能是立即完成，也可能是 timeout/pending；tray 端會再用 session polling 補結果。
        server_response = self.server.send(payload)
        diagnostics_payload = {
            "server_payload": safe_payload,
            "client_meta": {
                "mode": "thin_screenshot_sender",
                "ocr_location": "llmxx-server",
                "zero_disk_image_io": bool(self.config.get("app", {}).get("zero_disk_image_io", True)),
                "layout_region_count": len(layout_regions),
                "clinical_metadata_available": bool(clinical_metadata.get("icd10_code") or clinical_metadata.get("icd_code")),
                "clinical_metadata_source": str(clinical_metadata.get("metadata_source") or ""),
            },
        }
        diagnostics_path = None
        if self.config.get("app", {}).get("save_sanitized_diagnostics", True):
            diagnostics_path = str(
                write_diagnostics(
                    diagnostics_dir=self.config.get("app", {}).get("diagnostics_dir", "diagnostics"),
                    payload=safe_payload,
                    server_payload_path=None,
                    diagnostics_payload=diagnostics_payload,
                    verification=None,
                    server_response=server_response,
                )
            )
        return PipelineResult(
            payload=payload,
            diagnostics_payload=diagnostics_payload,
            server_payload_path=None,
            verification=None,
            server_response=server_response,
            diagnostics_path=diagnostics_path,
        )

    def _encode_png_base64(self, image_array: Any) -> str:
        try:
            from PIL import Image
        except Exception as exc:
            raise RuntimeError("薄截圖 client 需要 Pillow 來把 HIS 截圖轉成記憶體 PNG") from exc
        buffer = BytesIO()
        Image.fromarray(image_array).save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _read_clinical_metadata_sidecar(self) -> Dict[str, Any]:
        # ClinicalGuard sidecar 是 mock HIS 已知的結構化欄位，比從截圖 OCR 讀 ICD/A/P 穩定。
        # 如果 sidecar 不存在，仍回空 dict，server 會自行嘗試畫面 OCR fallback。
        configured_path = self.config.get("clinical_metadata", {}).get("sidecar_path", "")
        if configured_path:
            sidecar_path = Path(str(configured_path))
        else:
            apps_dir = Path(__file__).resolve().parents[3]
            sidecar_path = apps_dir / "clinicalguard-standalone" / "data" / "current_icd_selection.json"
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        safe: Dict[str, Any] = {}
        key_map = {
            "icd_code": "icd_code",
            "icd10_code": "icd10_code",
            "diagnosis_label": "diagnosis_label",
            "normalized_diagnosis": "normalized_diagnosis",
            "dx_text": "dx_text",
            "source": "metadata_source",
        }
        for source_key, target_key in key_map.items():
            value = str(data.get(source_key) or "").strip()
            if value:
                safe[target_key] = value[:240]
        soap = data.get("soap")
        if isinstance(soap, dict):
            safe_soap: Dict[str, str] = {}
            for key in ("S", "O", "A", "P"):
                value = str(soap.get(key) or "").strip()
                if value:
                    safe_soap[key] = value[:1200]
            if safe_soap:
                safe["soap"] = safe_soap
        vital_signs = data.get("vital_signs")
        if isinstance(vital_signs, dict):
            safe_vitals: Dict[str, str] = {}
            for key in ("bp", "hr", "temp", "rr", "spo2"):
                value = str(vital_signs.get(key) or "").strip()
                if value:
                    safe_vitals[key] = value[:40]
            if safe_vitals:
                safe["vital_signs"] = safe_vitals
        return safe

    def _build_layout_hints(self, *, window: Any, window_image: Any) -> list[dict[str, Any]]:
        # layout hints 是 server OCR 的「裁切建議」，不是可信病歷資料。
        # server 仍會做 bounds check；client 端提供 hints 只是讓 OCR 更穩。
        hints: list[dict[str, Any]] = []
        try:
            soap_regions = self.capture.resolve_regions(window=window, window_image=window_image)
        except Exception:
            soap_regions = self.capture.build_regions(window)
        try:
            vital_regions = self.capture.resolve_vital_regions(window=window, window_image=window_image, soap_regions=soap_regions)
        except Exception:
            vital_regions = []
        icd_region = self._detect_icd_region_hint(window=window, soap_regions=soap_regions)
        for region in ([icd_region] if icd_region is not None else []) + list(soap_regions) + list(vital_regions):
            hints.append(
                {
                    "field_name": region.field_name,
                    "x": max(0, int(region.left - window.left)),
                    "y": max(0, int(region.top - window.top)),
                    "w": max(1, int(region.width)),
                    "h": max(1, int(region.height)),
                    "method": str(region.detection_method),
                }
            )
        return hints

    def _detect_icd_region_hint(self, *, window: Any, soap_regions: list[Any]) -> CaptureRegion | None:
        # ICD 欄位在 SOAP 區塊上方，且是單行 Entry。這裡只根據 Tk child rect 估位置，
        # 不讀取文字內容；文字仍由 sidecar 或 server OCR 決定。
        try:
            child_rects = self.capture._enumerate_visible_child_rects(window)
        except Exception:
            return None
        if not child_rects:
            return None
        first_soap_top = min((region.top for region in soap_regions), default=window.top + int(window.height * 0.20))
        candidates: list[tuple[int, int, int, int]] = []
        for left, top, width, height in child_rects:
            rel_left = left - window.left
            if (
                window.top + int(window.height * 0.08) <= top <= first_soap_top - 4
                and 0.18 <= width / max(1, window.width) <= 0.55
                and 0.018 <= height / max(1, window.height) <= 0.06
                and 0.02 <= rel_left / max(1, window.width) <= 0.55
            ):
                candidates.append((left, top, width, height))
        if not candidates:
            return None
        left, top, width, height = sorted(candidates, key=lambda item: (-item[2], item[1], item[0]))[0]
        return CaptureRegion(
            field_name="icd_code",
            left=int(left),
            top=int(top),
            width=max(1, int(width)),
            height=max(1, int(height)),
            detection_method="client_layout_hint_icd",
        )



