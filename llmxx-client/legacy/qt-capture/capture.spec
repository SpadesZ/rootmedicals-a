# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['capture.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # 確保 PyInstaller 能找到這些動態載入的模組
    hiddenimports=['PySide6', 'mss', 'PIL', 'win32gui', 'win32ui', 'win32con'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Rootmedicals_Client', # 更新打包後的執行檔名稱
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # console=False 確保執行時不會跳出黑色的 CMD 視窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['pokers.ico'], # 沿用您原本的圖示 (確保圖示檔案在此目錄下)
)