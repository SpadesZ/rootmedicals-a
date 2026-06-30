# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/mock_his_typer.py
# 產生時間: 2026-06-18 11:45 +08:00
# 版本: v0.15
# 模組定位:
#   工程測試用的 ClinicalGuard 自動填值工具。它會在實際可見 UI 上點擊與貼上文字，讓閉環測試
#   盡量接近使用者操作，而不是直接改 Python 內部狀態。
# 主要責任:
#   1. 讀取 expected SOAP 測試文字。
#   2. 找到 ClinicalGuard 目前視窗與欄位區域。
#   3. 以滑鼠/剪貼簿填入 S/O/A/P 與生命徵象，並回報點擊座標供排查。
# 維護提醒:
#   - 這是測試輔助，不是正式產品功能；正式醫師端仍由人手輸入或 HIS 資料帶入。
#   - 這支檔案會短暫使用剪貼簿，務必在 finally 還原原本內容。
#   - 若 ClinicalGuard 版面調整，請先看 _resolve_current_regions，不要直接改固定座標。
# 驗證方式:
#   - fill-mock-his 後，畫面應能看到 SOAP/vitals 已填入，且 Ctrl+Alt+G 可接續送審。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from .capture import SoapFieldCapture
from .verification import load_expected_soap
from .window_locator import WindowLocator, enable_dpi_awareness


class MockHISTyper:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.locator = WindowLocator(config.get("target_window", {}))
        self.capture = SoapFieldCapture(config.get("capture", {}))

    def fill_from_expected_file(self, expected_path: str) -> Tuple[Dict[str, str], List[Dict[str, int | str]]]:
        soap = load_expected_soap(expected_path)
        click_points = self.fill_soap(soap)
        return soap, click_points

    def fill_soap(self, soap: Dict[str, str]) -> List[Dict[str, int | str]]:
        return self.fill_encounter(soap=soap, vital_signs={})

    def fill_encounter(self, soap: Dict[str, str], vital_signs: Dict[str, str] | None = None) -> List[Dict[str, int | str]]:
        enable_dpi_awareness()
        window = self.locator.find_target_window()
        soap_regions, vital_regions = self._resolve_current_regions(window)

        try:
            import win32api
            import win32con
            import win32clipboard
            import win32gui
        except Exception as exc:
            raise RuntimeError("pywin32 is required for mock HIS typing") from exc

        old_clipboard_text = _get_clipboard_text(win32clipboard)
        filled_points: List[Dict[str, int | str]] = []
        try:
            # 測試開始前先清掉殘留彈窗與上一輪文字，否則後續 OCR/RAG 問題會被 UI 狀態污染。
            _dismiss_mock_dialogs(win32gui, win32con)
            clear_point = _click_mock_clear_form(win32api, win32con, win32gui, window.hwnd)
            if clear_point:
                filled_points.append(clear_point)
            time.sleep(0.35)
            for field_name in ("S", "O", "A", "P"):
                region = soap_regions[field_name]
                text = str(soap.get(field_name, "") or "")
                # 測試時點在每個文字框左側，再貼上固定文字，確保每次閉環測試輸入一致。
                click_x = region.left + 24
                click_y = region.top + max(16, min(32, region.height // 2))
                _click_and_focus(win32api, win32con, click_x, click_y)
                # Tk 多行 Text 不一定穩定接受 Ctrl+A，因此清空時要走較保守的刪除流程。
                # 這段會從文件起點選到終點再刪除，避免上一輪 SOAP 文字殘留影響閉環判斷。
                _clear_multiline_text(win32api, win32con)
                if text:
                    _set_clipboard_text(win32clipboard, text)
                    _hotkey(win32api, win32con, "V")
                time.sleep(0.18)
                filled_points.append({"field_name": field_name, "click_x": click_x, "click_y": click_y})
            for field_name in ("bp", "hr", "temp", "rr", "spo2"):
                text = str((vital_signs or {}).get(field_name, "") or "")
                region = vital_regions.get(field_name)
                if region is None:
                    if text:
                        raise RuntimeError(f"Vital Signs field region not found: {field_name}")
                    continue
                # Vital Signs 是單行 Tk Entry，填值方式與 SOAP 多行 Text 不同。
                # 測試仍然從可見 UI 輸入，讓薄客戶端必須截圖讀像素，而不是讀 Python 內部狀態。
                # 這裡沿用 SOAP 的重試貼上流程；單純鍵盤事件在不同輸入法或焦點狀態下較容易失敗。
                # 視窗快速移動或焦點重設時，鍵盤事件也比較容易被 Tk 吃掉。
                click_x = region.left + 12
                click_y = region.top + max(8, region.height // 2)
                _click_and_focus(win32api, win32con, click_x, click_y)
                _clear_singleline_entry(win32api, win32con)
                if text:
                    _set_clipboard_text(win32clipboard, text)
                    _hotkey(win32api, win32con, "V")
                time.sleep(0.12)
                filled_points.append({"field_name": field_name, "click_x": click_x, "click_y": click_y})
        finally:
            if old_clipboard_text is not None:
                _set_clipboard_text(win32clipboard, old_clipboard_text)
        return filled_points

    def _resolve_current_regions(self, window: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        # 測試填值使用與實際截圖相同的自動區域邏輯，避免視窗移動後測試仍誤判通過。
        # 這樣 resize/move mock HIS 也會被驗證流程涵蓋，不會被固定座標掩蓋問題。
        window_image = self.capture.capture_window_image(window)
        soap_region_list = self.capture.resolve_regions(window=window, window_image=window_image)
        vital_region_list = self.capture.resolve_vital_regions(
            window=window,
            window_image=window_image,
            soap_regions=soap_region_list,
        )
        soap_regions = {region.field_name: region for region in soap_region_list}
        vital_regions = {region.field_name: region for region in vital_region_list}
        return soap_regions, vital_regions


def _click_mock_clear_form(win32api: Any, win32con: Any, win32gui: Any, hwnd: int) -> Dict[str, int | str] | None:
    # ClinicalGuard_Standalone 是固定 Tk mock HIS，底部按鈕列可用固定規則推估。
    # 若抓不到文字，才用「底部同高度按鈕列最右側」作為 Clear Form 的保守 fallback。
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    window_height = max(1, bottom - top)
    candidates: List[tuple[int, int, int, int]] = []
    text_matches: List[tuple[int, int, int, int]] = []

    def collect(child_hwnd: int, _: object) -> bool:
        c_left, c_top, c_right, c_bottom = win32gui.GetWindowRect(child_hwnd)
        width = c_right - c_left
        height = c_bottom - c_top
        title = str(win32gui.GetWindowText(child_hwnd) or "").strip().lower()
        if title == "clear form" and width > 20 and height > 12:
            text_matches.append((c_left, c_top, c_right, c_bottom))
        rel_top = (c_top - top) / window_height
        if 0.82 <= rel_top <= 0.96 and 45 <= width <= 150 and 18 <= height <= 35:
            candidates.append((c_left, c_top, c_right, c_bottom))
        return True

    win32gui.EnumChildWindows(hwnd, collect, None)
    if text_matches:
        c_left, c_top, c_right, c_bottom = text_matches[0]
        click_x = (c_left + c_right) // 2
        click_y = (c_top + c_bottom) // 2
        win32api.SetCursorPos((click_x, click_y))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, click_x, click_y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, click_x, click_y, 0, 0)
        time.sleep(0.25)
        return {"field_name": "ClearForm", "click_x": click_x, "click_y": click_y}

    if not candidates:
        return None

    bottom_top = max(item[1] for item in candidates)
    bottom_row = [item for item in candidates if abs(item[1] - bottom_top) <= 4]
    ordered_candidates = sorted(bottom_row or candidates, key=lambda item: item[0])
    if len(ordered_candidates) >= 4 and (ordered_candidates[-1][0] - ordered_candidates[-2][0]) > 180:
        c_left, c_top, c_right, c_bottom = ordered_candidates[-2]
    else:
        c_left, c_top, c_right, c_bottom = ordered_candidates[-1]
    click_x = (c_left + c_right) // 2
    click_y = (c_top + c_bottom) // 2
    win32api.SetCursorPos((click_x, click_y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, click_x, click_y, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, click_x, click_y, 0, 0)
    time.sleep(0.25)
    return {"field_name": "ClearForm", "click_x": click_x, "click_y": click_y}


def _dismiss_mock_dialogs(win32gui: Any, win32con: Any) -> None:
    dialog_titles = {
        "Input Error",
        "Clinical Guard",
        "Clinical Guard Warning",
        "Sync Result",
        "Export Error",
        "Export Success",
        "PID Options",
    }

    def close_if_matching(hwnd: int, _: object) -> bool:
        title = str(win32gui.GetWindowText(hwnd) or "").strip()
        if title in dialog_titles:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return True

    win32gui.EnumWindows(close_if_matching, None)
    time.sleep(0.2)


def _hotkey(win32api: Any, win32con: Any, letter: str) -> None:
    vk = ord(letter.upper())
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(vk, 0, 0, 0)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def _click_and_focus(win32api: Any, win32con: Any, click_x: int, click_y: int) -> None:
    # 短暫的雙擊式 focus 流程可降低重複 GUI 測試時焦點飄移。
    # mock HIS 剛移動或剛清空時，這能避免文字被貼到上一個仍持有焦點的欄位。
    win32api.SetCursorPos((click_x, click_y))
    time.sleep(0.08)
    for _ in range(2):
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, click_x, click_y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, click_x, click_y, 0, 0)
        time.sleep(0.06)
    time.sleep(0.12)


def _clear_multiline_text(win32api: Any, win32con: Any) -> None:
    # 清空欄位行為刻意寫明，未來換真 HIS adapter 時可直接替換這段。
    # 其餘 self-verification 流程仍可沿用，不會因為換 HIS adapter 而整段重寫。
    # Tk Text 的選取行為會受系統與輸入法影響，因此保留多重清空路徑。
    # 重複自動驗證時，舊 SOAP 文字殘留會造成假成功，所以這裡寧可多做一次清空。
    _hotkey(win32api, win32con, "A")
    time.sleep(0.05)
    _press_key(win32api, win32con, win32con.VK_BACK)
    time.sleep(0.05)
    _key_combo(win32api, win32con, [win32con.VK_CONTROL], win32con.VK_HOME)
    time.sleep(0.05)
    _key_combo(win32api, win32con, [win32con.VK_CONTROL, win32con.VK_SHIFT], win32con.VK_END)
    time.sleep(0.05)
    _press_key(win32api, win32con, win32con.VK_BACK)
    time.sleep(0.08)
    _hotkey(win32api, win32con, "A")
    time.sleep(0.05)
    _press_key(win32api, win32con, win32con.VK_BACK)
    time.sleep(0.08)


def _clear_singleline_entry(win32api: Any, win32con: Any) -> None:
    # Tk Entry 通常能穩定處理 Ctrl+A，和多行 Text 不同。
    _hotkey(win32api, win32con, "A")
    time.sleep(0.04)
    _press_key(win32api, win32con, win32con.VK_BACK)
    time.sleep(0.05)


def _type_ascii_text(win32api: Any, win32con: Any, text: str) -> None:
    # Vital Signs 是數字 ASCII，直接鍵入可避開剪貼簿短暫被占用的問題。
    # 目前主流程多數仍用剪貼簿貼上；此 helper 保留給未來需要避開剪貼簿時使用。
    for char in str(text):
        code = win32api.VkKeyScan(char)
        if code == -1:
            continue
        vk = code & 0xFF
        shift_state = (code >> 8) & 0xFF
        if shift_state & 1:
            win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
        win32api.keybd_event(vk, 0, 0, 0)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        if shift_state & 1:
            win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.015)


def _key_combo(win32api: Any, win32con: Any, modifiers: List[int], key: int) -> None:
    for modifier in modifiers:
        win32api.keybd_event(modifier, 0, 0, 0)
    win32api.keybd_event(key, 0, 0, 0)
    win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
    for modifier in reversed(modifiers):
        win32api.keybd_event(modifier, 0, win32con.KEYEVENTF_KEYUP, 0)


def _press_key(win32api: Any, win32con: Any, key: int) -> None:
    win32api.keybd_event(key, 0, 0, 0)
    win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)


def _get_clipboard_text(win32clipboard: Any) -> str | None:
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(13):
                return win32clipboard.GetClipboardData(13)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _set_clipboard_text(win32clipboard: Any, text: str) -> None:
    # 自動化 UI 測試時 Windows 剪貼簿可能短暫被其他程式占用，因此需要重試。
    # SOAP 貼上只做短次數重試；超過就明確失敗，避免整套測試卡住不回應。
    # 空欄位用「清空 widget」表示，不貼入空白字串，避免 HIS 留下不可見字元。
    # 這也能避開 Windows 偶發的 invalid-handle 剪貼簿錯誤。
    if not str(text):
        return
    last_exc: Exception | None = None
    for _ in range(10):
        opened = False
        try:
            win32clipboard.OpenClipboard()
            opened = True
            win32clipboard.EmptyClipboard()
            if hasattr(win32clipboard, "SetClipboardText"):
                win32clipboard.SetClipboardText(str(text), 13)
            else:
                win32clipboard.SetClipboardData(13, str(text))
            return
        except Exception as exc:
            last_exc = exc
            time.sleep(0.1)
        finally:
            if opened:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
    if last_exc:
        raise last_exc


