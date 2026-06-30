# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/pyinstaller_hooks/pre_find_module_path/hook-tkinter.py
# 產生時間: 2026-06-18 10:40 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard PyInstaller pre-find hook。它在 PyInstaller 決定 tkinter module path 前執行，
#   用來避免建置主機 Tcl 探測失敗時被誤排除。
# 主要責任:
#   1. 保持 tkinter 的 module search path 不被預設排除邏輯改壞。
#   2. 讓後續 hook-tkinter.py 與 runtime hook 有機會正常收集/設定 Tcl/Tk。
# 維護提醒:
#   - 這支 hook 故意不做任何路徑改寫；若要補資料檔，請改 build.ps1。
#   - 若 PyInstaller 新版修正 tkinter 探測，可保留此檔，因為空操作不會影響正常分析。
# 驗證方式:
#   - build.ps1 folder 模式能成功分析 tkinter，且打包版啟動不報 Tcl/Tk 初始化錯誤。
# ----------------------------------------------------------------------------------------------------

"""
PyInstaller 分析前保留 tkinter 查找路徑。
"""


def pre_find_module_path(hook_api):
    # 維護筆記：這裡保持空操作是有意設計。只要不把 tkinter 排除，後續 hooks 就能收集需要的模組。
    return

