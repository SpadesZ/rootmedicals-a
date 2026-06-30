# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/pyinstaller_hooks/rthook_set_tk_paths.py
# 產生時間: 2026-06-18 10:35 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard 打包後的 PyInstaller runtime hook。Tkinter 在 frozen exe 啟動時需要先知道
#   Tcl/Tk runtime 資料夾位置，否則醫師端視窗會在 import tkinter 或建立 root window 時失敗。
# 主要責任:
#   1. 從 PyInstaller 解壓目錄或 exe 目錄找出 Tcl/Tk runtime。
#   2. 設定 TCL_LIBRARY / TK_LIBRARY 環境變數。
#   3. 不碰任何 GUI 邏輯、病患資料、網路或 server endpoint。
# 維護提醒:
#   - 若未來升級 Python/Tk 或調整 PyInstaller add-data 目的地，請同步更新候選路徑。
#   - 這支檔案會在 app 程式碼之前執行，請保持無副作用、無外部依賴、無使用者互動。
# 驗證方式:
#   - 用 build.ps1 產出 folder 版後直接啟動 ClinicalGuard.exe。
# ----------------------------------------------------------------------------------------------------

"""
設定打包版 ClinicalGuard 的 Tcl/Tk runtime 路徑。
"""

from __future__ import annotations

import os
import sys


def _pick_existing(paths):
    for p in paths:
        if os.path.isdir(p):
            return p
    return ""


# PyInstaller 執行時會把資料解壓到 sys._MEIPASS；若不是 frozen app，則退回 exe 目錄。
# 這樣同一支 hook 在打包版與除錯版都能安全執行。
base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

# 路徑順序要與 build.ps1 的 --add-data 目的地保持一致，前兩個是目前正式打包路徑，
# 後面的 tcl/tcl8.6 與 tcl8.6 是給手動打包或舊版 spec 的相容 fallback。
tcl_dir = _pick_existing(
    [
        os.path.join(base, "_tcl_data"),
        os.path.join(base, "tcl", "tcl8.6"),
        os.path.join(base, "tcl8.6"),
    ]
)

tk_dir = _pick_existing(
    [
        os.path.join(base, "_tk_data"),
        os.path.join(base, "tcl", "tk8.6"),
        os.path.join(base, "tk8.6"),
    ]
)

if tcl_dir:
    # Tkinter import 前設定環境變數，避免 Tcl 初始化時找不到 init.tcl。
    os.environ["TCL_LIBRARY"] = tcl_dir
if tk_dir:
    # TK_LIBRARY 找不到時通常會出現視窗啟動失敗，而不是 later runtime error，所以需在最早期處理。
    os.environ["TK_LIBRARY"] = tk_dir

