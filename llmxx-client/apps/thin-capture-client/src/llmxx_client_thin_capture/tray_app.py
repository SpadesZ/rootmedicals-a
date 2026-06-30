# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/tray_app.py
# 產生時間: 2026-06-18 11:02 +08:00
# 版本: v0.3
# 模組定位:
#   醫師端常駐 tray app。它把 Ctrl+Alt+G、手動擷取、doctor alert widget 與 session polling
#   串成使用者看得到的閉環入口。
# 主要責任:
#   1. 建立系統匣選單與 Qt event loop。
#   2. 註冊 Windows 原生 hotkey 或 pynput fallback。
#   3. 擷取送出後把 server 結果交給 DoctorAlertWidget 顯示。
# 維護提醒:
#   - tray thread 與 Qt UI thread 分工要保持清楚；不要在 hotkey callback 直接操作 widget。
#   - 醫師端只顯示結果，不解釋 RAG 來源是否可信；可信度文字由 server response 決定。
# 驗證方式:
#   - 以 RootMedicals-Control 啟動後，Ctrl+Alt+G 應出現「送出中」再更新燈號。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import os
import subprocess
import threading
import traceback
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .doctor_alert_widget import DoctorAlertWidget
from .pipeline import ThinCapturePipeline


class TrayClientApp:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.pipeline = ThinCapturePipeline(config)
        self._lock = threading.Lock()
        self._hotkey_listener = None
        self._native_hotkey_thread = None
        self._native_hotkey_thread_id = None
        self._log_path = Path(str(config.get("_project_root", "."))).resolve() / "runtime" / "tray_client.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def run(self) -> int:
        try:
            from PySide6.QtCore import QObject, Signal
            from PySide6.QtGui import QAction, QIcon
            from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon
        except Exception as exc:
            raise RuntimeError("PySide6 is required for tray mode") from exc

        class Signals(QObject):
            capture_requested = Signal(str)
            notify_requested = Signal(str, str)
            alert_requested = Signal(dict)

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        signals = Signals()
        alert_widget = DoctorAlertWidget(self.config)

        icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        tray = QSystemTrayIcon(icon if not icon.isNull() else QIcon(), app)
        tray.setToolTip("llmxx-client-thin-capture")

        menu = QMenu()
        capture_action = QAction("Run Capture Now")
        diagnostics_action = QAction("Open Diagnostics Folder")
        quit_action = QAction("Quit")
        menu.addAction(capture_action)
        menu.addAction(diagnostics_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)

        capture_action.triggered.connect(lambda: signals.capture_requested.emit("tray"))
        tray.activated.connect(lambda reason: signals.capture_requested.emit("tray-double-click") if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        diagnostics_action.triggered.connect(self._open_diagnostics_folder)
        quit_action.triggered.connect(lambda: self._quit(app))
        signals.capture_requested.connect(lambda source: self._start_capture(source, signals))
        signals.notify_requested.connect(lambda title, message: tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 6000))
        signals.alert_requested.connect(alert_widget.show_event)

        tray.show()
        binding = str(self.config.get("hotkey", {}).get("binding", "<ctrl>+<alt>+g"))
        # Hotkey listener 可能跑在非 Qt thread，因此只發 signal，由 Qt thread 接手 UI 更新。
        self._start_hotkey_listener(binding, signals)
        self._log(f"tray active binding={binding}")
        tray.showMessage("llmxx-client-thin-capture", f"Tray active. Hotkey: {binding}", QSystemTrayIcon.MessageIcon.Information, 4000)
        return int(app.exec())

    def _start_hotkey_listener(self, binding: str, signals: Any) -> None:
        if self._start_windows_hotkey_listener(binding, signals):
            return
        try:
            from pynput import keyboard
        except Exception as exc:
            raise RuntimeError("pynput is required for tray hotkey mode") from exc

        # Hotkey callback 只丟 Qt signal；截圖與 HTTP 送出在 worker thread，避免鍵盤 hook 卡死。
        self._hotkey_listener = keyboard.GlobalHotKeys({binding: lambda: self._on_hotkey(signals)})
        self._hotkey_listener.start()
        self._log("pynput hotkey listener active")

    def _start_windows_hotkey_listener(self, binding: str, signals: Any) -> bool:
        normalized = binding.strip().lower().replace(" ", "")
        if os.name != "nt" or normalized not in {"<ctrl>+<alt>+g", "<alt>+<ctrl>+g"}:
            return False
        ready = threading.Event()
        result = {"ok": False}
        worker = threading.Thread(
            target=self._windows_hotkey_loop,
            args=(signals, ready, result),
            daemon=True,
        )
        worker.start()
        ready.wait(timeout=1.5)
        if bool(result.get("ok")):
            self._native_hotkey_thread = worker
            self._log("windows RegisterHotKey listener active")
            return True
        self._log(f"windows RegisterHotKey listener unavailable: {result.get('error', 'unknown')}")
        return False

    def _windows_hotkey_loop(self, signals: Any, ready: threading.Event, result: Dict[str, Any]) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hotkey_id = 0x524D
        mod_alt = 0x0001
        mod_control = 0x0002
        wm_hotkey = 0x0312
        self._native_hotkey_thread_id = int(kernel32.GetCurrentThreadId())
        if not user32.RegisterHotKey(None, hotkey_id, mod_alt | mod_control, ord("G")):
            result["error"] = f"RegisterHotKey failed winerror={ctypes.get_last_error()}"
            ready.set()
            return
        result["ok"] = True
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if int(msg.message) == wm_hotkey and int(msg.wParam) == hotkey_id:
                    # Windows 原生 hotkey 比 pynput 穩定；收到訊息後仍走同一條 _on_hotkey 流程。
                    self._on_hotkey(signals)
        finally:
            user32.UnregisterHotKey(None, hotkey_id)

    def _on_hotkey(self, signals: Any) -> None:
        self._log("hotkey received")
        signals.capture_requested.emit("hotkey")

    def _start_capture(self, source: str, signals: Any) -> None:
        if not self._lock.acquire(blocking=False):
            self._log(f"capture skipped busy source={source}")
            signals.notify_requested.emit("llmxx-client-thin-capture", "Previous capture is still running.")
            signals.alert_requested.emit({"type": "busy", "message": "上一筆擷取仍在處理中，請稍候。"})
            return
        self._log(f"capture start source={source}")
        signals.alert_requested.emit({"type": "hide"})
        signals.notify_requested.emit("llmxx-client-thin-capture", f"{source}: 已觸發，正在截圖並送往 llmxx-server OCR/RAG。")
        worker = threading.Thread(target=self._run_capture_worker, args=(source, signals), daemon=True)
        worker.start()

    def _run_capture_worker(self, source: str, signals: Any) -> None:
        try:
            result = self.pipeline.run_once()
            diagnostics = result.diagnostics_path or ""
            status = result.server_response.get("status") if isinstance(result.server_response, dict) else "unknown"
            self._log(f"capture complete source={source} status={status} diagnostics={diagnostics}")
            if isinstance(result.server_response, dict):
                signals.alert_requested.emit({"type": "response", "source": source, "response": result.server_response})
                self._start_session_polling(result.server_response, signals)
            signals.notify_requested.emit(
                "llmxx-client-thin-capture",
                f"Capture complete from {source}. Send status: {status}. Diagnostics: {Path(diagnostics).name if diagnostics else 'none'}",
            )
        except Exception:
            self._log("capture failed\n" + traceback.format_exc())
            signals.notify_requested.emit("llmxx-client-thin-capture", "Capture failed. See terminal for traceback.")
            signals.alert_requested.emit({"type": "error", "source": source, "message": "擷取或送出失敗，請查看終端機 traceback。"})
            print(traceback.format_exc())
        finally:
            self._lock.release()

    def _start_session_polling(self, response: Dict[str, Any], signals: Any) -> None:
        session_id = str(response.get("session_id") or "").strip()
        if not session_id:
            return
        alert_config = self.config.get("doctor_alert", {}) if isinstance(self.config.get("doctor_alert"), dict) else {}
        if not bool(alert_config.get("poll_enabled", True)):
            return
        poll_seconds = float(alert_config.get("poll_seconds", 30.0) or 30.0)
        interval_seconds = float(alert_config.get("poll_interval_seconds", 2.0) or 2.0)
        worker = threading.Thread(
            target=self._poll_session_worker,
            args=(session_id, poll_seconds, interval_seconds, signals),
            daemon=True,
        )
        worker.start()

    def _poll_session_worker(self, session_id: str, poll_seconds: float, interval_seconds: float, signals: Any) -> None:
        deadline = time.monotonic() + max(0.0, poll_seconds)
        last_response_signature = ""
        while time.monotonic() < deadline:
            time.sleep(max(0.5, interval_seconds))
            try:
                payload = self.pipeline.server.get_session(session_id)
            except Exception:
                continue
            response = payload.get("response") if isinstance(payload, dict) else None
            if not isinstance(response, dict) or not response:
                continue
            final_gate = response.get("final_gate") if isinstance(response.get("final_gate"), dict) else {}
            signature = "|".join(
                [
                    str(response.get("status") or ""),
                    str(response.get("error_code") or ""),
                    str(final_gate.get("light_color") or ""),
                    str(final_gate.get("display_mode") or ""),
                ]
            )
            if signature != last_response_signature:
                last_response_signature = signature
                signals.alert_requested.emit({"type": "poll", "session_id": session_id, "response": response})
            if response.get("status") == "completed":
                break

    def _log(self, message: str) -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        try:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} {message}\n")
        except Exception:
            pass

    def _open_diagnostics_folder(self) -> None:
        diagnostics_dir = Path(str(self.config.get("app", {}).get("diagnostics_dir", "diagnostics"))).resolve()
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            subprocess.Popen(["explorer", str(diagnostics_dir)])
        else:
            subprocess.Popen(["xdg-open", str(diagnostics_dir)])

    def _quit(self, app: Any) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
        if self._native_hotkey_thread_id is not None:
            try:
                import ctypes

                ctypes.windll.user32.PostThreadMessageW(int(self._native_hotkey_thread_id), 0x0012, 0, 0)
            except Exception:
                pass
        app.quit()
