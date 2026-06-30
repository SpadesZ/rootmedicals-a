# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/hotkey_runner.py
# 產生時間: 2026-06-18 10:52 +08:00
# 版本: v0.3
# 模組定位:
#   終端機模式的全域 hotkey runner。Ctrl+Alt+G 只負責觸發一次 thin screenshot capture，
#   醫師端浮窗、OCR、RAG 與 final gate 都由 pipeline/server 後續處理。
# 主要責任:
#   1. 註冊全域快捷鍵。
#   2. 防止使用者連按 hotkey 時產生重疊送出。
#   3. 將診斷路徑與 server response 印到終端，供工程人員排查。
# 維護提醒:
#   - Hotkey callback 不應直接做耗時工作，否則會卡住 listener；耗時流程放在背景 thread。
#   - 若要改快捷鍵，優先改 config，不要在程式碼硬編新的組合鍵。
# 驗證方式:
#   - 啟動 hotkey 模式後連按 Ctrl+Alt+G，應只跑一個 capture，第二次會被略過。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import threading
import traceback
from typing import Any, Dict

from .pipeline import ThinCapturePipeline


class HotkeyRunner:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.pipeline = ThinCapturePipeline(config)
        self._lock = threading.Lock()

    def run_forever(self) -> None:
        try:
            from pynput import keyboard
        except Exception as exc:
            raise RuntimeError("pynput is required for global hotkey mode") from exc

        binding = str(self.config.get("hotkey", {}).get("binding", "<ctrl>+<alt>+g"))
        print(f"[llmxx-client-thin-capture] Hotkey mode active: {binding}")
        print("[llmxx-client-thin-capture] Press Ctrl+C in this terminal to stop.")

        with keyboard.GlobalHotKeys({binding: self._on_hotkey}) as listener:
            listener.join()

    def _on_hotkey(self) -> None:
        if not self._lock.acquire(blocking=False):
            # 防重入很重要：同一張 HIS 畫面若同時送兩次，doctor alert 可能被後回來的結果覆蓋。
            print("[llmxx-client-thin-capture] Previous capture is still running; skipped.")
            return
        # pynput callback 要快速返回；實際擷取與 HTTP 呼叫放到背景執行緒。
        thread = threading.Thread(target=self._run_capture_safe, daemon=True)
        thread.start()

    def _run_capture_safe(self) -> None:
        try:
            result = self.pipeline.run_once()
            print("[llmxx-client-thin-capture] Capture done.")
            print(json.dumps({"diagnostics_path": result.diagnostics_path, "server_response": result.server_response}, ensure_ascii=False, indent=2))
        except Exception:
            # 終端模式保留完整 traceback，tray 模式則會改由醫師端浮窗顯示簡短錯誤。
            print("[llmxx-client-thin-capture] Capture failed:")
            print(traceback.format_exc())
        finally:
            self._lock.release()

