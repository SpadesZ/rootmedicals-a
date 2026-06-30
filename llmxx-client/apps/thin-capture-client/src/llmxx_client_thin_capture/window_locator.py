# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/window_locator.py
# 產生時間: 2026-06-18 11:05 +08:00
# 版本: v0.2
# 模組定位:
#   thin capture client 的 Windows 視窗定位器。它負責找到 ClinicalGuard/HIS 視窗，並盡量讓
#   截圖座標與螢幕像素一致。
# 主要責任:
#   1. 啟用 DPI awareness，降低 Windows 縮放造成的座標偏差。
#   2. 用 title_contains 找目標視窗。
#   3. 必要時 restore/bring-to-front，讓 mss 擷取到可見畫面。
# 維護提醒:
#   - 這層只碰 Win32 window handle 與座標，不應讀取欄位文字或保存截圖。
#   - 若正式 HIS 標題會變動，請改 config 的 title_contains，不要在程式碼硬編醫院名稱。
# 驗證方式:
#   - 啟動 ClinicalGuard 後 run-once --no-send，可在 diagnostics 看到正確 window bounds。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import ctypes
import time
from typing import Dict, Iterable, List

from .models import WindowInfo


def enable_dpi_awareness() -> None:
    """Make Win32 window coordinates match screenshot coordinates where possible."""
    try:
        # Per-monitor DPI aware 優先，讓 125%/150% 縮放環境下座標較接近 mss 實際截圖。
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return


class WindowLocator:
    def __init__(self, target_config: Dict):
        self.target_config = target_config

    def find_target_window(self) -> WindowInfo:
        title_needles = [
            str(item).lower()
            for item in self.target_config.get("title_contains", [])
            if str(item).strip()
        ]
        if not title_needles:
            raise ValueError("target_window.title_contains is empty")

        windows = self._list_visible_windows()
        for window in windows:
            title_lower = window.title.lower()
            if any(needle in title_lower for needle in title_needles):
                # 找到視窗後先準備可見狀態，再重新取 rect，避免 restore 前後尺寸不同。
                self._prepare_window(window.hwnd)
                return self._get_window_info(window.hwnd, window.title)

        known = ", ".join(w.title for w in windows[:20])
        # 錯誤訊息列出前幾個可見視窗，方便現場排查 title_contains 設定是否太窄。
        raise RuntimeError(f"Target window not found. First visible windows: {known}")

    def _list_visible_windows(self) -> List[WindowInfo]:
        try:
            import win32gui
        except Exception as exc:
            raise RuntimeError("pywin32 is required for Windows window discovery") from exc

        out: List[WindowInfo] = []

        def callback(hwnd: int, _: object) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            try:
                out.append(self._get_window_info(hwnd, title))
            except Exception:
                return True
            return True

        win32gui.EnumWindows(callback, None)
        return [w for w in out if w.width > 0 and w.height > 0]

    def _prepare_window(self, hwnd: int) -> None:
        try:
            import win32con
            import win32gui
        except Exception:
            return

        if self.target_config.get("restore_if_minimized", True):
            try:
                if win32gui.IsIconic(hwnd):
                    # mss 無法從最小化視窗擷取有效內容，因此先還原。
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.2)
            except Exception:
                pass

        if self.target_config.get("bring_to_front", True):
            try:
                # mss 擷取的是可見 framebuffer，因此目標視窗不能被其他視窗遮住。
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
                )
                time.sleep(0.1)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.2)
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_NOTOPMOST,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
                )
                time.sleep(0.2)
            except Exception:
                pass

    @staticmethod
    def _get_window_info(hwnd: int, title: str) -> WindowInfo:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return WindowInfo(
            hwnd=int(hwnd),
            title=str(title),
            left=int(left),
            top=int(top),
            right=int(right),
            bottom=int(bottom),
        )



