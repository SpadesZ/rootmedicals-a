# 檔案路徑: rootmedicals-a/llmxx-client/apps/local-ocr/src/llmxx_client_local_ocr/preprocess.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: LocalOCR 客戶端程式，負責截圖、OCR/薄客戶端傳送、醫師端提醒視窗與驗證工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# Path: ./llmxx-client-local-ocr/src/llmxx_client_local_ocr/preprocess.py
# Version History:
# v0.1 20260614-0000 - Initial in-memory OCR preprocessing controls.

from __future__ import annotations

from typing import Any, Dict


def preprocess_for_ocr(image_array: Any, preprocess_config: Dict) -> Any:
    """Preprocess a RAM image for OCR without saving intermediate files."""
    if not preprocess_config.get("enabled", True):
        return image_array

    try:
        import cv2
    except Exception as exc:
        raise RuntimeError("opencv-python is required for preprocessing") from exc

    image = image_array
    scale_factor = float(preprocess_config.get("scale_factor", 1.0) or 1.0)
    if scale_factor > 1.0:
        image = cv2.resize(image, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)

    if preprocess_config.get("grayscale", True):
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        if preprocess_config.get("denoise", False):
            gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        if preprocess_config.get("adaptive_threshold", False):
            gray = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                11,
            )
        # Inner v0.1: Convert back to RGB because EasyOCR accepts consistent image shapes across versions.
        image = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

    return image

