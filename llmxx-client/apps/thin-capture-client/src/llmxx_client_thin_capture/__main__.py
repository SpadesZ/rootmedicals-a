# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/__main__.py
# 產生時間: 2026-06-18 11:48 +08:00
# 版本: v0.2
# 模組定位:
#   thin capture client 的 `python -m llmxx_client_thin_capture` 入口。
# 主要責任:
#   - 將 module execution 轉交給 main.py，避免 CLI 邏輯散在兩個入口。
# 維護提醒:
#   - 新增命令列功能時請改 main.py，不要在這裡分支。
# 驗證方式:
#   - python -m llmxx_client_thin_capture print-config
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from .main import main


if __name__ == "__main__":
    raise SystemExit(main())



