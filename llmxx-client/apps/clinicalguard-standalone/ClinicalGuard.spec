# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/ClinicalGuard.spec
# 產生時間: 2026-06-18 10:45 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard 的 PyInstaller spec 檔。此檔可作為打包參考，但正式建置仍建議優先執行
#   build.ps1，因為 build.ps1 會依目前 Python 環境重新解析 Tcl/Tk runtime 路徑。
# 主要責任:
#   1. 描述 ClinicalGuard GUI 打包需要的 entrypoint、hiddenimports、Tcl/Tk data 與 runtime hook。
#   2. 保留可追查的 PyInstaller 分析設定，方便建置失敗時比對 build.ps1 產出的參數。
# 維護提醒:
#   - 本檔含本機絕對路徑；移到其他機器時請由 build.ps1 重新產生，或同步更新下方路徑。
#   - 若只交付原始碼執行，不需要直接執行這份 spec。
# 驗證方式:
#   - build.ps1 folder mode 能成功產生 ClinicalGuard.exe。
# ----------------------------------------------------------------------------------------------------

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['tkinter', '_tkinter', 'tkinter.ttk', 'tkinter.messagebox']
hiddenimports += collect_submodules('tkinter')


a = Analysis(
    ['C:\\Users\\Franky Kuo\\Desktop\\rootmedicals\\rootmedicals-a\\llmxx-client\\apps\\clinicalguard-standalone\\src\\clinical_guard_app\\gui_app.py'],
    pathex=['C:\\Users\\Franky Kuo\\Desktop\\rootmedicals\\rootmedicals-a\\llmxx-client\\apps\\clinicalguard-standalone\\src'],
    binaries=[('C:\\Users\\Franky Kuo\\AppData\\Local\\Programs\\Python\\Python310\\DLLs\\tk86t.dll', '.'), ('C:\\Users\\Franky Kuo\\AppData\\Local\\Programs\\Python\\Python310\\DLLs\\tcl86t.dll', '.')],
    datas=[('C:\\Users\\Franky Kuo\\AppData\\Local\\Programs\\Python\\Python310\\tcl\\tcl8.6', '_tcl_data'), ('C:\\Users\\Franky Kuo\\AppData\\Local\\Programs\\Python\\Python310\\tcl\\tk8.6', '_tk_data'), ('C:\\Users\\Franky Kuo\\AppData\\Local\\Programs\\Python\\Python310\\tcl\\tcl8', 'tcl8'), ('C:\\Users\\Franky Kuo\\AppData\\Local\\Programs\\Python\\Python310\\Lib\\tkinter', 'tkinter')],
    hiddenimports=hiddenimports,
    hookspath=['C:\\Users\\Franky Kuo\\Desktop\\rootmedicals\\rootmedicals-a\\llmxx-client\\apps\\clinicalguard-standalone\\pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=['C:\\Users\\Franky Kuo\\Desktop\\rootmedicals\\rootmedicals-a\\llmxx-client\\apps\\clinicalguard-standalone\\pyinstaller_hooks\\rthook_set_tk_paths.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClinicalGuard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClinicalGuard',
)
