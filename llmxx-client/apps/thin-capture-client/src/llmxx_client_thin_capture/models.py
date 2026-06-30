# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/models.py
# 產生時間: 2026-06-18 11:10 +08:00
# 版本: v0.3
# 模組定位:
#   thin capture client 的本機資料模型。這些 dataclass 描述視窗、截圖區域、診斷與 pipeline 結果，
#   不承載 OCR 文字，也不承載臨床判斷結果。
# 主要責任:
#   1. 統一 window bounds、capture region、field capture 的資料形狀。
#   2. 提供 diagnostics/self-check 使用的 VerificationResult。
#   3. 包裝 pipeline 單次執行後要回傳給 CLI/tray 的結果。
# 維護提醒:
#   - 若要新增 OCR text、Dx/Tx/Hx 或燈號模型，請放在 llmxx-server contracts，不要放回 client。
#   - FieldCapture.image_array 只在記憶體內流動，診斷檔不可保存完整影像內容。
# 驗證方式:
#   - py_compile。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WindowInfo:
    # Win32 回傳的是螢幕絕對座標；width/height 用 property 計算，避免四個邊界不同步。
    hwnd: int
    title: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class CaptureRegion:
    # region 是給 server OCR 的裁切提示，不代表該區文字已被 client 讀取或驗證。
    field_name: str
    left: int
    top: int
    width: int
    height: int
    detection_method: str = "configured_relative"

    def to_dict(self) -> Dict[str, int | str]:
        return {
            "field_name": self.field_name,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "detection_method": self.detection_method,
        }


@dataclass
class FieldCapture:
    field_name: str
    region: CaptureRegion
    image_array: Any


@dataclass
class VerificationResult:
    passed: bool
    score_by_field: Dict[str, float]
    expected: Dict[str, str]
    actual: Dict[str, str]
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score_by_field": self.score_by_field,
            "expected": self.expected,
            "actual": self.actual,
            "notes": self.notes,
        }


@dataclass
class PipelineResult:
    # server_payload_path 保留相容欄位；thin client 不再產生 formal OCR payload，因此通常是 None。
    payload: Dict[str, Any]
    diagnostics_payload: Dict[str, Any]
    server_payload_path: Optional[str]
    verification: Optional[VerificationResult]
    server_response: Optional[Dict[str, Any]]
    diagnostics_path: Optional[str]
