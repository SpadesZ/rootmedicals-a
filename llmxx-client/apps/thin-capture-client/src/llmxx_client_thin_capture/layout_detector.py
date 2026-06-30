# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/layout_detector.py
# 產生時間: 2026-06-18 11:12 +08:00
# 版本: v0.3
# 模組定位:
#   SOAP layout detector。它從目前 HIS 視窗影像中找出 S/O/A/P 四個大文字框，讓 server OCR
#   可以優先裁切正確區域。
# 主要責任:
#   1. 用 OpenCV 找出疑似白底文字框。
#   2. 選出最像 SOAP 垂直排列的四個候選框。
#   3. 回傳 CaptureRegion，不讀取文字內容。
# 維護提醒:
#   - 這是對 ClinicalGuard 版面友善的 detector，不是通用 HIS parser。
#   - 影像判斷失敗時應回到 configured regions，不能讓醫師端熱鍵流程直接中斷。
# 驗證方式:
#   - 調整視窗大小後 run-once --no-send，確認 diagnostics 仍包含 S/O/A/P layout hints。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Sequence, Tuple

from .models import CaptureRegion, WindowInfo


class SoapLayoutDetector:
    def __init__(self, detector_config: Dict[str, Any]):
        self.detector_config = detector_config

    def detect_regions(self, *, window: WindowInfo, image_array: Any) -> List[CaptureRegion]:
        """Detect S/O/A/P text boxes from the current window image without saving pixels to disk."""
        try:
            import cv2
        except Exception as exc:
            raise RuntimeError("opencv-python is required for automatic SOAP layout detection") from exc

        height, width = image_array.shape[:2]
        if width <= 0 or height <= 0:
            return []

        gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
        white_min = int(self.detector_config.get("white_threshold", 245) or 245)
        # ClinicalGuard 的 SOAP 輸入框是大片白底；先抓高亮區域，再用比例條件排除非文字框。
        mask = cv2.inRange(gray, white_min, 255)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates: List[Tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if self._looks_like_soap_text_box(x, y, w, h, width, height):
                candidates.append((x, y, w, h))

        layout_boxes = sorted(candidates, key=lambda box: (box[1], box[0]))
        try:
            import layoutparser as lp
        except Exception:
            # LayoutParser 在這裡只是把候選框包成一致資料型別；真正的候選框來自 OpenCV，
            # 所以沒有 LayoutParser 時仍可使用 OpenCV 結果，不必退回較舊的固定座標。
            pass
        else:
            layout = lp.Layout(
                [
                    lp.TextBlock(lp.Rectangle(x, y, x + w, y + h), type="soap_candidate", score=1.0)
                    for x, y, w, h in layout_boxes
                ]
            )
            layout_boxes = [
                (int(block.block.x_1), int(block.block.y_1), int(block.block.width), int(block.block.height))
                for block in layout
            ]

        chosen = self._choose_best_four_boxes(layout_boxes)
        if len(chosen) != 4:
            # 寧可回傳空集合讓上層 fallback，也不要把錯誤欄位硬當 SOAP 送給 server OCR。
            return []

        regions: List[CaptureRegion] = []
        for field_name, (x, y, w, h) in zip(("S", "O", "A", "P"), chosen):
            regions.append(
                CaptureRegion(
                    field_name=field_name,
                    left=window.left + x,
                    top=window.top + y,
                    width=max(1, w),
                    height=max(1, h),
                    detection_method="auto_cv2_soap_box",
                )
            )
        return regions

    def _looks_like_soap_text_box(self, x: int, y: int, w: int, h: int, image_width: int, image_height: int) -> bool:
        min_width_ratio = float(self.detector_config.get("min_width_ratio", 0.55) or 0.55)
        min_height_ratio = float(self.detector_config.get("min_height_ratio", 0.045) or 0.045)
        max_height_ratio = float(self.detector_config.get("max_height_ratio", 0.14) or 0.14)
        min_y_ratio = float(self.detector_config.get("min_y_ratio", 0.22) or 0.22)
        max_y_ratio = float(self.detector_config.get("max_y_ratio", 0.84) or 0.84)

        width_ratio = w / max(1, image_width)
        height_ratio = h / max(1, image_height)
        y_ratio = y / max(1, image_height)
        return (
            width_ratio >= min_width_ratio
            and min_height_ratio <= height_ratio <= max_height_ratio
            and min_y_ratio <= y_ratio <= max_y_ratio
        )

    def _choose_best_four_boxes(self, boxes: Sequence[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
        if len(boxes) < 4:
            return []

        sorted_boxes = sorted(boxes, key=lambda box: (box[1], box[0]))
        best_score: float | None = None
        best_group: List[Tuple[int, int, int, int]] = []
        for start in range(0, len(sorted_boxes) - 3):
            # SOAP 四格在畫面上連續排列；用滑動窗口比對相鄰四格，避免抓到上方 Patient/ICD 欄位。
            group = list(sorted_boxes[start:start + 4])
            score = self._score_group(group)
            if best_score is None or score < best_score:
                best_score = score
                best_group = group
        return best_group

    def _score_group(self, group: Sequence[Tuple[int, int, int, int]]) -> float:
        xs = [box[0] for box in group]
        ys = [box[1] for box in group]
        widths = [box[2] for box in group]
        heights = [box[3] for box in group]
        gaps = [ys[idx + 1] - ys[idx] for idx in range(len(ys) - 1)]

        # SOAP 四格在 mock HIS 中應該垂直排列，且 x/寬/高非常接近；分數越低代表越像目標欄位。
        x_penalty = _mean_abs_delta(xs)
        width_penalty = _mean_abs_delta(widths)
        height_penalty = _mean_abs_delta(heights)
        gap_penalty = _mean_abs_delta(gaps)
        return x_penalty + width_penalty + height_penalty + gap_penalty


def _mean_abs_delta(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    center = mean(values)
    return float(sum(abs(value - center) for value in values) / len(values))


