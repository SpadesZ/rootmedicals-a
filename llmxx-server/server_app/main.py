# 檔案路徑: rootmedicals-a/llmxx-server/server_app/main.py
# 產生時間: 2026-06-18 11:50 +08:00
# 版本: v0.3
# 模組定位:
#   llmxx-server 的 FastAPI 相容入口。實際 route 在 api/main.py；此檔只保留
#   `uvicorn server_app.main:app` 的啟動契約。
# 主要責任:
#   - 匯出 app symbol，讓 Start-LLMXX-Server、Dockerfile、舊 smoke test 不受目錄分類影響。
# 維護提醒:
#   - 不要在這裡新增 route 或背景任務；請放到 api/main.py 或對應 core/integrations 模組。
# 驗證方式:
#   - uvicorn server_app.main:app 可啟動，/api/health 回 online。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

# 程式筆記：真正的 route 與閉環邏輯已整理到 api/main.py；這裡只保留
# 對外 app 符號，避免啟動腳本、Dockerfile、舊 smoke test 因目錄整理失效。
from .api.main import app

__all__ = ["app"]
