# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/mock_his_launcher.py
# 產生時間: 2026-06-18 11:15 +08:00
# 版本: v0.5
# 模組定位:
#   thin capture client 的 ClinicalGuard 啟動輔助器。RootMedicals-Control 會透過它開啟本機
#   醫師端視窗，讓 Ctrl+Alt+G 有穩定的目標視窗可以擷取。
# 主要責任:
#   1. 找到 sibling clinicalguard-standalone app。
#   2. 建立 runtime launcher，修正打包/搬移後的路徑推導。
#   3. 以目前 Python 啟動互動式 Tk 視窗。
# 維護提醒:
#   - 這裡只啟動測試 HIS，不改 ClinicalGuard 原始碼，也不參與 OCR/RAG/final gate。
#   - runtime launcher 是暫存橋接檔，寫在 runtime 目錄；若啟動失敗，看 data/gui_launch_error.log。
# 驗證方式:
#   - RootMedicals-Control 的 Start Live RAG / Start Demo Fixture 都應能開啟 ClinicalGuard 視窗。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict


def launch_mock_his(project_root: str | Path) -> Dict[str, str | int]:
    root = Path(project_root).resolve()
    mock_root = root.parent / "clinicalguard-standalone"
    src_dir = mock_root / "src"
    db_path = mock_root / "data" / "test_guard_open.db"
    export_dir = mock_root / "data" / "test_exports"
    runtime_dir = root / "runtime"
    launcher_path = runtime_dir / "clinical_guard_launch.py"
    if not src_dir.exists():
        raise FileNotFoundError(f"clinicalguard-standalone source not found: {src_dir}")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(src_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # runtime launcher 讓 ClinicalGuard 在不安裝 package 的情況下啟動；這降低 demo 機器的環境要求。
    _write_runtime_launcher(launcher_path=launcher_path, mock_root=mock_root, db_path=db_path, export_dir=export_dir)
    executable = Path(sys.executable)
    args = [str(executable), str(launcher_path)]
    # 啟動固定 mock HIS 作為 sibling process；thin capture client 與 HIS 原始碼保持隔離。
    # 這裡只在 runtime launcher 的記憶體模組中修正路徑，不回寫 ClinicalGuard 檔案。
    # 好處是 demo 啟動穩定，但正式 HIS adapter 之後仍可直接替換，不會被 mock 程式耦合住。
    process = subprocess.Popen(args, cwd=str(mock_root), env=env)
    return {
        "status": "started",
        "pid": int(process.pid),
        "mock_root": str(mock_root),
        "db_path": str(db_path),
        "export_dir": str(export_dir),
        "launcher_path": str(launcher_path),
    }


def _write_runtime_launcher(*, launcher_path: Path, mock_root: Path, db_path: Path, export_dir: Path) -> None:
    # 這段 source 會被寫成臨時啟動檔。它只在 runtime 執行，不回寫原始碼；主要處理搬目錄後
    # local_core._detect_repo_root 對 parents 深度的假設。
    source = f"""import sys, types, traceback
from pathlib import Path
root = Path(r'{mock_root}')
try:
    pkg = types.ModuleType('clinical_guard_app')
    pkg.__path__ = [str(root / 'src' / 'clinical_guard_app')]
    sys.modules['clinical_guard_app'] = pkg
    local_path = root / 'src' / 'clinical_guard_app' / 'local_core.py'
    local_src = local_path.read_text(encoding='utf-8').replace('return p.parents[5]', 'return p.parents[2]')
    local_mod = types.ModuleType('clinical_guard_app.local_core')
    local_mod.__file__ = str(local_path)
    local_mod.__package__ = 'clinical_guard_app'
    sys.modules['clinical_guard_app.local_core'] = local_mod
    exec(compile(local_src, str(local_path), 'exec'), local_mod.__dict__)
    gui_path = root / 'src' / 'clinical_guard_app' / 'gui_app.py'
    gui_src = gui_path.read_text(encoding='utf-8')
    gui_mod = types.ModuleType('clinical_guard_app.gui_app')
    gui_mod.__file__ = str(gui_path)
    gui_mod.__package__ = 'clinical_guard_app'
    sys.modules['clinical_guard_app.gui_app'] = gui_mod
    exec(compile(gui_src, str(gui_path), 'exec'), gui_mod.__dict__)
    raise SystemExit(gui_mod.main(['--db', r'{db_path}', '--export-dir', r'{export_dir}']))
except Exception:
    (root / 'data' / 'gui_launch_error.log').write_text(traceback.format_exc(), encoding='utf-8')
    raise
"""
    launcher_path.write_text(source, encoding="utf-8")


