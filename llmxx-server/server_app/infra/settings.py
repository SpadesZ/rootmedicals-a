# 檔案路徑: rootmedicals-a/llmxx-server/server_app/infra/settings.py
# 產生時間: 2026-06-18 11:28 +08:00
# 版本: v0.2
# 模組定位:
#   llmxx-server 的集中設定檔。這裡把資料庫路徑、RAG/LAVA endpoint、timeout、安全開關與
#   Demo Fixture mode 收斂在同一個 dataclass。
# 主要責任:
#   1. 解析 rootmedicals-a 內部路徑。
#   2. 從環境變數讀取 runtime 開關。
#   3. 提供 ensure_runtime_dirs 給 server 啟動流程建立必要資料夾。
# 維護提醒:
#   - 這裡只定義設定，不執行 HTTP、OCR 或臨床判斷。
#   - timeout 建議保守調整；RAG timeout 會造成 yellow/review，不應用高 timeout 卡死醫師端。
#   - Demo Fixture 是明確展示模式，不應與 production live RAG 混用。
# 驗證方式:
#   - 啟動 /api/health 可看到 server online。
#   - 切換 RootMedicals-Control 的 Live RAG / Demo Fixture 後，env 設定應反映在 /demo/latest 結果。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = PACKAGE_DIR.parent
ROOTMEDICALS_A_DIR = SERVER_DIR.parent
DATA_DIR = SERVER_DIR / "data"
STATIC_DIR = SERVER_DIR / "static"
DEFAULT_DB_PATH = DATA_DIR / "llmxx_server.db"
DEFAULT_REDACTED_ARCHIVE_DIR = ROOTMEDICALS_A_DIR / "shared_data" / "llmxx_redacted_payload_archive"


def _bool_from_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    # 支援常見部署寫法，避免 Windows/PowerShell 與 Docker env 寫法不一致。
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float_from_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        # 環境變數填錯時採用預設值，避免 server 啟動失敗；實際 timeout 問題會在 health/log 中呈現。
        return default


@dataclass(frozen=True)
class Settings:
    # RAG 與 LAVA endpoint 預設都指向本機 ebm-rag stack；正式部署時用 env 覆蓋。
    service_name: str = "llmxx-server"
    app_version: str = "0.1.0"
    db_path: Path = DEFAULT_DB_PATH
    rag_check_url: str = os.environ.get("LLMXX_RAG_CHECK_URL", "http://127.0.0.1:33301/api/v1/rag/check")
    lava_task_base_url: str = os.environ.get("LLMXX_LAVA_TASK_BASE_URL", "http://127.0.0.1:33301/api/lava/tasks")
    request_timeout_seconds: float = _float_from_env("LLMXX_SERVER_TIMEOUT_SECONDS", 90.0)
    rag_timeout_seconds: float = _float_from_env("LLMXX_RAG_TIMEOUT_SECONDS", 75.0)
    rag_background_timeout_seconds: float = _float_from_env("LLMXX_RAG_BACKGROUND_TIMEOUT_SECONDS", 150.0)
    lava_timeout_seconds: float = _float_from_env("LLMXX_LAVA_TIMEOUT_SECONDS", 2.0)
    max_top_k: int = 50
    default_top_k: int = 10
    payload_archive_enabled: bool = _bool_from_env("LLMXX_PAYLOAD_ARCHIVE_ENABLED", False)
    redacted_archive_dir: Path = DEFAULT_REDACTED_ARCHIVE_DIR
    aes_key_env_var: str = os.environ.get("LLMXX_SERVER_AES256_KEY_B64", "LLMXX_SERVER_AES256_KEY_B64")
    patient_hmac_key_env_var: str = os.environ.get("LLMXX_PATIENT_HMAC_KEY", "LLMXX_PATIENT_HMAC_KEY")
    allow_localhost_http: bool = _bool_from_env("LLMXX_ALLOW_LOCALHOST_HTTP", True)
    # Demo Fixture 只用新的環境變數控制，避免舊命名混進交付文件與維運腳本。
    demo_fixture_mode: bool = _bool_from_env("LLMXX_DEMO_FIXTURE_MODE", False)
    rag_demo_synthetic_fallback: bool = _bool_from_env("LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK", False)
    rag_query_strategy_mode: str = os.environ.get("LLMXX_RAG_QUERY_STRATEGY_MODE", "llm_assisted")


settings = Settings()


def ensure_runtime_dirs() -> None:
    # 啟動時只建立必要資料夾，不預先建立 archive，除非 payload archive 明確啟用。
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if settings.payload_archive_enabled:
        settings.redacted_archive_dir.mkdir(parents=True, exist_ok=True)

