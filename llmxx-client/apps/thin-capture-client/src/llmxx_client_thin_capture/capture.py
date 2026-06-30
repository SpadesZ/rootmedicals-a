# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/capture.py
# 產生時間: 2026-06-18 10:55 +08:00
# 版本: v0.3
# 模組定位:
#   thin client 的畫面擷取與 layout hint 產生器。它只處理像素與座標，不讀文字、不做 OCR、
#   不解釋 A/P/ICD，也不決定燈號。
# 主要責任:
#   1. 擷取 ClinicalGuard 視窗影像到記憶體。
#   2. 嘗試用 Win32 child controls / layout detector / config fallback 找 SOAP 與 vitals 區塊。
#   3. 回傳 server OCR 可使用的欄位位置提示。
# 維護提醒:
#   - layout hint 是「裁切建議」，不是可信臨床資料；server 必須做 bounds check 與 OCR 驗證。
#   - ClinicalGuard UI 若調整高度、欄距或欄位順序，請同步跑一次 Ctrl+Alt+G 實測。
# 驗證方式:
#   - run-once --no-send 應產出 diagnostics，且 layout hints 覆蓋 S/O/A/P/vitals。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .layout_detector import SoapLayoutDetector
from .models import CaptureRegion, FieldCapture, WindowInfo


class SoapFieldCapture:
    def __init__(self, capture_config: Dict):
        self.capture_config = capture_config
        self.detector = SoapLayoutDetector(capture_config.get("layout_detection", {}))

    def capture_fields(self, window: WindowInfo) -> List[FieldCapture]:
        """Capture configured SOAP fields from screen pixels without writing image files."""
        window_image = self.capture_window_image(window)
        # 先從完整視窗畫面偵測 SOAP 方框，避免視窗移動或小幅改版時完全依賴固定座標。
        regions = self.resolve_regions(window=window, window_image=window_image)
        regions.extend(self.resolve_vital_regions(window=window, window_image=window_image, soap_regions=regions))
        captures: List[FieldCapture] = []
        for region in regions:
            x1 = max(0, region.left - window.left)
            y1 = max(0, region.top - window.top)
            x2 = min(window_image.shape[1], x1 + max(1, region.width))
            y2 = min(window_image.shape[0], y1 + max(1, region.height))
            captures.append(FieldCapture(field_name=region.field_name, region=region, image_array=window_image[y1:y2, x1:x2].copy()))
        return captures

    def capture_window_image(self, window: WindowInfo) -> Any:
        try:
            import mss
            import numpy as np
        except Exception as exc:
            raise RuntimeError("mss and numpy are required for RAM screenshot capture") from exc

        monitor = {
            "left": window.left,
            "top": window.top,
            "width": max(1, window.width),
            "height": max(1, window.height),
        }
        with mss.mss() as sct:
            shot = sct.grab(monitor)
            bgra = np.array(shot)
            # mss 回傳 BGRA；統一轉成 RGB，讓後續 PNG encode 與 server OCR 都吃同一種色彩順序。
            return bgra[:, :, [2, 1, 0]]

    def resolve_regions(self, *, window: WindowInfo, window_image: Any | None = None) -> List[CaptureRegion]:
        mode = str(self.capture_config.get("mode", "window_relative_soap_fields"))
        detected_from_controls = self._detect_win32_soap_regions(window)
        if len(detected_from_controls) == 4:
            # Win32 child rects 最接近真實 UI control，優先使用；失敗才退到影像或設定座標。
            return detected_from_controls
        if mode == "auto_layout_soap_fields" and window_image is not None:
            try:
                detected = self.detector.detect_regions(window=window, image_array=window_image)
                if len(detected) == 4:
                    return detected
            except Exception:
                # layout detection 是輔助能力；若設定允許 fallback，就回到固定相對座標，避免整個熱鍵流程中斷。
                if not bool(self.capture_config.get("fallback_to_configured_regions", True)):
                    raise
        return self.build_regions(window)

    def _detect_win32_soap_regions(self, window: WindowInfo) -> List[CaptureRegion]:
        child_rects = self._enumerate_visible_child_rects(window)
        if not child_rects or window.width <= 0 or window.height <= 0:
            return []

        candidates: List[Tuple[int, int, int, int]] = []
        for left, top, width, height in child_rects:
            rel_left = left - window.left
            rel_top = top - window.top
            left_ratio = rel_left / max(1, window.width)
            top_ratio = rel_top / max(1, window.height)
            width_ratio = width / max(1, window.width)
            height_ratio = height / max(1, window.height)
            if (
                0.02 <= left_ratio <= 0.30
                and 0.55 <= width_ratio <= 0.98
                and 0.045 <= height_ratio <= 0.18
                and 0.14 <= top_ratio <= 0.75
            ):
                # 這些比例是針對 ClinicalGuard SOAP 大型文字框；新版 HIS demo 左側多了科別資訊欄，
                # 因此 left_ratio 上限放寬到 0.30，但仍要求足夠寬度，避免抓到按鈕或狀態列。
                candidates.append((rel_left, rel_top, width, height))

        chosen = self.detector._choose_best_four_boxes(candidates)
        if len(chosen) != 4:
            return []

        out: List[CaptureRegion] = []
        for field_name, (rel_left, rel_top, width, height) in zip(("S", "O", "A", "P"), chosen):
            out.append(
                CaptureRegion(
                    field_name=field_name,
                    left=window.left + rel_left,
                    top=window.top + rel_top,
                    width=max(1, width),
                    height=max(1, height),
                    detection_method="auto_win32_child_soap",
                )
            )
        return out

    def build_regions(self, window: WindowInfo) -> List[CaptureRegion]:
        if window.width <= 0 or window.height <= 0:
            raise ValueError("Window has invalid dimensions")

        field_regions = self.capture_config.get("field_regions", {})
        if not isinstance(field_regions, dict) or not field_regions:
            raise ValueError("capture.field_regions is empty")

        out: List[CaptureRegion] = []
        for field_name in ("S", "O", "A", "P"):
            item = field_regions.get(field_name)
            if not isinstance(item, dict):
                raise ValueError(f"Missing capture region for SOAP field {field_name}")

            x = float(item.get("x", 0.0))
            y = float(item.get("y", 0.0))
            w = float(item.get("w", 0.0))
            h = float(item.get("h", 0.0))
            region = CaptureRegion(
                field_name=field_name,
                left=window.left + int(round(window.width * x)),
                top=window.top + int(round(window.height * y)),
                width=max(1, int(round(window.width * w))),
                height=max(1, int(round(window.height * h))),
                detection_method="configured_relative",
            )
            out.append(region)
        return out

    def resolve_vital_regions(
        self,
        *,
        window: WindowInfo,
        window_image: Any,
        soap_regions: List[CaptureRegion],
    ) -> List[CaptureRegion]:
        vital_config = self.capture_config.get("vital_signs", {})
        if not bool(vital_config.get("enabled", True)):
            return []

        detected_from_controls = self._detect_win32_vital_regions(window=window, soap_regions=soap_regions)
        if len(detected_from_controls) == 5:
            return detected_from_controls

        mode = str(vital_config.get("mode", "auto_vital_entry_fields"))
        if mode == "auto_vital_entry_fields":
            detected = self._detect_vital_entry_regions(window=window, window_image=window_image, soap_regions=soap_regions)
            if len(detected) == 5:
                return detected

        return self.build_vital_regions(window)

    def _detect_win32_vital_regions(self, *, window: WindowInfo, soap_regions: List[CaptureRegion]) -> List[CaptureRegion]:
        child_rects = self._enumerate_visible_child_rects(window)
        if not child_rects or window.width <= 0 or window.height <= 0:
            return []

        p_bottom = max((region.top + region.height for region in soap_regions), default=window.top)
        min_top = p_bottom + int(window.height * 0.018)
        max_top = window.top + int(window.height * 0.82)
        candidates: List[Tuple[int, int, int, int]] = []
        for left, top, width, height in child_rects:
            rel_left = left - window.left
            rel_top = top - window.top
            width_ratio = width / max(1, window.width)
            height_ratio = height / max(1, window.height)
            if (
                min_top <= top <= max_top
                and 0.06 <= width_ratio <= 0.16
                and 0.015 <= height_ratio <= 0.04
            ):
                candidates.append((rel_left, rel_top, width, height))

        best_row: List[Tuple[int, int, int, int]] = []
        best_score: float | None = None
        for candidate in sorted(candidates, key=lambda item: (item[1], item[0])):
            row = [box for box in candidates if abs(box[1] - candidate[1]) <= max(8, int(window.height * 0.012))]
            if len(row) < 5:
                continue
            row = sorted(row, key=lambda item: item[0])[:5]
            y_values = [box[1] for box in row]
            widths = [box[2] for box in row]
            score = (max(y_values) - min(y_values)) + (max(widths) - min(widths))
            if best_score is None or score < best_score:
                best_score = float(score)
                best_row = row

        if len(best_row) != 5:
            return []

        out: List[CaptureRegion] = []
        for field_name, (rel_left, rel_top, width, height) in zip(("bp", "hr", "temp", "rr", "spo2"), best_row):
            out.append(
                CaptureRegion(
                    field_name=field_name,
                    left=window.left + rel_left,
                    top=window.top + rel_top,
                    width=max(1, width),
                    height=max(1, height),
                    detection_method="auto_win32_child_vital",
                )
            )
        return out

    def build_vital_regions(self, window: WindowInfo) -> List[CaptureRegion]:
        vital_config = self.capture_config.get("vital_signs", {})
        field_regions = vital_config.get("field_regions", {})
        out: List[CaptureRegion] = []
        for field_name in ("bp", "hr", "temp", "rr", "spo2"):
            item = field_regions.get(field_name)
            if not isinstance(item, dict):
                continue
            x = float(item.get("x", 0.0))
            y = float(item.get("y", 0.0))
            w = float(item.get("w", 0.0))
            h = float(item.get("h", 0.0))
            out.append(
                CaptureRegion(
                    field_name=field_name,
                    left=window.left + int(round(window.width * x)),
                    top=window.top + int(round(window.height * y)),
                    width=max(1, int(round(window.width * w))),
                    height=max(1, int(round(window.height * h))),
                    detection_method="configured_relative_vital",
                )
            )
        return out

    def _detect_vital_entry_regions(
        self,
        *,
        window: WindowInfo,
        window_image: Any,
        soap_regions: List[CaptureRegion],
    ) -> List[CaptureRegion]:
        try:
            import cv2
        except Exception as exc:
            raise RuntimeError("opencv-python is required for Vital Signs layout detection") from exc

        gray = cv2.cvtColor(window_image, cv2.COLOR_RGB2GRAY)
        height, width = gray.shape
        vital_config = self.capture_config.get("vital_signs", {})
        white_min = int(vital_config.get("white_threshold", 245) or 245)
        mask = cv2.inRange(gray, white_min, 255)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        p_bottom = 0
        if soap_regions:
            p_bottom = max((region.top + region.height - window.top for region in soap_regions), default=0)

        candidates: List[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if (
                p_bottom + int(height * 0.025) <= y <= int(height * 0.90)
                and 0.055 <= (w / max(1, width)) <= 0.16
                and 0.016 <= (h / max(1, height)) <= 0.04
            ):
                candidates.append((x, y, w, h))

        if len(candidates) < 5:
            return []

        best_row: List[tuple[int, int, int, int]] = []
        best_score: float | None = None
        for candidate in sorted(candidates, key=lambda item: (item[1], item[0])):
            row = [box for box in candidates if abs(box[1] - candidate[1]) <= max(8, int(height * 0.012))]
            if len(row) < 5:
                continue
            row = sorted(row, key=lambda item: item[0])[:5]
            y_values = [box[1] for box in row]
            widths = [box[2] for box in row]
            score = (max(y_values) - min(y_values)) + (max(widths) - min(widths))
            if best_score is None or score < best_score:
                best_score = float(score)
                best_row = row

        if len(best_row) != 5:
            return []

        out: List[CaptureRegion] = []
        for field_name, (x, y, w, h) in zip(("bp", "hr", "temp", "rr", "spo2"), best_row):
            out.append(
                CaptureRegion(
                    field_name=field_name,
                    left=window.left + x,
                    top=window.top + y,
                    width=max(1, w),
                    height=max(1, h),
                    detection_method="auto_vital_cv2",
                )
            )
        return out

    def _enumerate_visible_child_rects(self, window: WindowInfo) -> List[Tuple[int, int, int, int]]:
        if window.hwnd <= 0:
            return []
        try:
            import win32gui
        except Exception:
            return []

        rects: List[Tuple[int, int, int, int]] = []

        def collect(child_hwnd: int, _: object) -> bool:
            try:
                if not bool(win32gui.IsWindowVisible(child_hwnd)):
                    return True
                left, top, right, bottom = win32gui.GetWindowRect(child_hwnd)
            except Exception:
                return True
            width = max(0, int(right) - int(left))
            height = max(0, int(bottom) - int(top))
            if width > 0 and height > 0:
                rects.append((int(left), int(top), width, height))
            return True

        try:
            win32gui.EnumChildWindows(window.hwnd, collect, None)
        except Exception:
            return []
        return rects


