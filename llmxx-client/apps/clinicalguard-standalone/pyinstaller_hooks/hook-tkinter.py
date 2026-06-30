# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/pyinstaller_hooks/hook-tkinter.py
# 產生時間: 2026-06-18 10:38 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard PyInstaller analysis hook。它在打包分析階段執行，目的不是改 runtime 行為，
#   而是確保 tkinter 子模組被收進 frozen app。
# 主要責任:
#   1. 強制收集 tkinter 相關 submodules。
#   2. 補足不同 Windows/Python 安裝環境中 PyInstaller 偵測 tkinter 不穩的問題。
# 維護提醒:
#   - 若打包後 GUI 啟動缺少 tkinter.ttk 或 messagebox，先檢查這裡的 hiddenimports。
#   - Tcl/Tk 資料檔位置由 build.ps1 與 rthook_set_tk_paths.py 處理，不要混在這支 hook。
# 驗證方式:
#   - build.ps1 folder 模式成功產出 ClinicalGuard.exe，並能開啟 SOAP 視窗。
# ----------------------------------------------------------------------------------------------------

"""
打包分析階段收集 tkinter 子模組。
"""

from PyInstaller.utils.hooks import collect_submodules

# PyInstaller 有時會因建置主機 Tcl 探測失敗而漏收 tkinter 子模組；這裡採白名單式收集。
hiddenimports = collect_submodules("tkinter")

