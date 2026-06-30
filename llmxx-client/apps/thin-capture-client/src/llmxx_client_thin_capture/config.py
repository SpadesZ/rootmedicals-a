# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/config.py
# 產生時間: 2026-06-18 10:58 +08:00
# 版本: v0.3
# 模組定位:
#   thin capture client 的設定載入器。它把 JSON 設定轉成執行期可用的絕對路徑，並提供測試
#   expected SOAP template 的寫入工具。
# 主要責任:
#   1. 載入 config/default_config.json。
#   2. 將 diagnostics、verification 等專案相對路徑正規化。
#   3. 保持設定檔格式簡單，讓 RootMedicals-Control 可以切換 Live RAG / Demo Fixture。
# 維護提醒:
#   - 不要在 config loader 放醫學判斷或 OCR 參數推理；它只處理設定形狀與路徑。
#   - 若新增路徑型設定，請在 _normalize_project_relative_paths 明確轉成絕對路徑。
# 驗證方式:
#   - print-config 應顯示 _config_path、_project_root 與絕對 diagnostics_dir。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


def load_config(config_path: str | Path | None = None) -> Dict[str, Any]:
    """Load config and normalize project-relative paths."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    config = deepcopy(config)
    # 保留來源路徑，後續 diagnostics 與錯誤訊息才能清楚指出實際讀到哪份 config。
    config["_config_path"] = str(path)
    config["_project_root"] = str(PROJECT_ROOT)
    _normalize_project_relative_paths(config)
    return config


def _normalize_project_relative_paths(config: Dict[str, Any]) -> None:
    """Keep file path settings easy to maintain from JSON."""
    project_root = Path(config["_project_root"])

    app_cfg = config.get("app", {})
    diagnostics_dir = Path(str(app_cfg.get("diagnostics_dir", "diagnostics")))
    if not diagnostics_dir.is_absolute():
        # 設定檔維持短路徑，程式執行時轉絕對路徑，避免從不同 cwd 啟動時寫到錯的資料夾。
        app_cfg["diagnostics_dir"] = str((project_root / diagnostics_dir).resolve())

    verification_cfg = config.get("verification", {})
    expected_path = Path(str(verification_cfg.get("expected_soap_path", "")))
    if str(expected_path) and not expected_path.is_absolute():
        verification_cfg["expected_soap_path"] = str((project_root / expected_path).resolve())


def write_expected_template(path: str | Path, overwrite: bool = False) -> Path:
    """Create a small expected SOAP template for self-verification."""
    out_path = Path(path)
    if not out_path.is_absolute():
        out_path = (PROJECT_ROOT / out_path).resolve()
    if out_path.exists() and not overwrite:
        # 預設不覆蓋人工調整過的測試樣本，避免 demo 前把已驗證案例洗掉。
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sample = {
        "S": "headache for 3 days",
        "O": "alert and oriented",
        "A": "migraine suspected",
        "P": "acetaminophen",
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)
    return out_path
