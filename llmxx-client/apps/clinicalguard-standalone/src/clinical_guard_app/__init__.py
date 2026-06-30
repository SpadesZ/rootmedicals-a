# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/src/clinical_guard_app/__init__.py
# 產生時間: 2026-06-18 12:00 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard standalone package marker。GUI、ICD master helper 與 local_core 都從此 package 匯入。
# 維護提醒:
#   - 這裡不做啟動副作用，避免 PyInstaller analysis 或測試 import 時直接開視窗。
# ----------------------------------------------------------------------------------------------------

from .local_core import ClinicalGuardLocal

__all__ = ["ClinicalGuardLocal"]
