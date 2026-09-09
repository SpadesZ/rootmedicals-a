# 檔案路徑: rootmedicals-a/legacy/client-local-ocr-legacy/src/llmxx_client_local_ocr_legacy/ocr_engine.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: LocalOCR 客戶端程式，負責截圖、OCR/薄客戶端傳送、醫師端提醒視窗與驗證工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# Path: ./llmxx-client-local-ocr/src/llmxx_client_local_ocr/ocr_engine.py
# Version History:
# v0.1 20260614-0000 - Initial EasyOCR wrapper for S/O/A/P field recognition.
# v0.2 20260614-0000 - Add line-aware OCR block ordering to fix single-line word reordering.

from __future__ import annotations

from typing import Any, Dict, List

from .models import OCRBlock, OCRFieldResult


class EasyOCREngine:
    def __init__(self, ocr_config: Dict):
        self.ocr_config = ocr_config
        self._reader = None

    def load(self) -> None:
        if self._reader is not None:
            return
        try:
            import easyocr
        except Exception as exc:
            raise RuntimeError("easyocr is required for OCR") from exc

        languages = list(self.ocr_config.get("languages", ["ch_tra", "en"]))
        gpu = bool(self.ocr_config.get("gpu", False))
        self._reader = easyocr.Reader(languages, gpu=gpu)

    def recognize_field(self, field_name: str, image_array: Any) -> OCRFieldResult:
        self.load()
        assert self._reader is not None

        paragraph = bool(self.ocr_config.get("paragraph", False))
        min_confidence = float(self.ocr_config.get("min_confidence", 0.0) or 0.0)
        raw_results = self._reader.readtext(image_array, detail=1, paragraph=paragraph)

        blocks: List[OCRBlock] = []
        for row in raw_results:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            bbox = row[0]
            text = str(row[1] or "").strip()
            confidence = float(row[2]) if len(row) >= 3 else 0.72
            if not text or confidence < min_confidence:
                continue
            blocks.append(
                OCRBlock(
                    text=text,
                    confidence=round(confidence, 4),
                    bbox=_normalize_bbox(bbox),
                )
            )

        # Inner v0.2: group words into visual lines before sorting by x, because EasyOCR can return
        # slightly negative y values for the last word and otherwise move it to the beginning.
        blocks = _sort_blocks_by_reading_order(blocks)
        text = _normalize_joined_text([block.text for block in blocks])
        confidence = round(sum(block.confidence for block in blocks) / max(1, len(blocks)), 4)
        return OCRFieldResult(field_name=field_name, text=text, confidence=confidence, blocks=blocks)


def _normalize_bbox(value: Any) -> List[List[float]]:
    out: List[List[float]] = []
    if isinstance(value, list):
        for point in value[:4]:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                out.append([float(point[0]), float(point[1])])
    return out


def _bbox_top(bbox: List[List[float]]) -> float:
    return min((point[1] for point in bbox), default=0.0)


def _bbox_left(bbox: List[List[float]]) -> float:
    return min((point[0] for point in bbox), default=0.0)


def _bbox_center_y(bbox: List[List[float]]) -> float:
    top = min((point[1] for point in bbox), default=0.0)
    bottom = max((point[1] for point in bbox), default=0.0)
    return (top + bottom) / 2.0


def _bbox_height(bbox: List[List[float]]) -> float:
    top = min((point[1] for point in bbox), default=0.0)
    bottom = max((point[1] for point in bbox), default=0.0)
    return max(1.0, bottom - top)


def _sort_blocks_by_reading_order(blocks: List[OCRBlock]) -> List[OCRBlock]:
    if not blocks:
        return []

    sorted_by_y = sorted(blocks, key=lambda block: (_bbox_center_y(block.bbox), _bbox_left(block.bbox)))
    median_height = sorted(_bbox_height(block.bbox) for block in sorted_by_y)[len(sorted_by_y) // 2]
    same_line_tolerance = max(12.0, median_height * 0.75)

    lines: List[List[OCRBlock]] = []
    line_centers: List[float] = []
    for block in sorted_by_y:
        center_y = _bbox_center_y(block.bbox)
        placed = False
        for idx, existing_center in enumerate(line_centers):
            if abs(center_y - existing_center) <= same_line_tolerance:
                lines[idx].append(block)
                line_centers[idx] = (existing_center + center_y) / 2.0
                placed = True
                break
        if not placed:
            lines.append([block])
            line_centers.append(center_y)

    ordered: List[OCRBlock] = []
    for _, line in sorted(zip(line_centers, lines), key=lambda item: item[0]):
        ordered.extend(sorted(line, key=lambda block: _bbox_left(block.bbox)))
    return ordered


def _normalize_joined_text(items: List[str]) -> str:
    text = " ".join(str(item).strip() for item in items if str(item).strip())
    return " ".join(text.split())
