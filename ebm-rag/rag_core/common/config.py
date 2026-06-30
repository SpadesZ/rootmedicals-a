# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/common/config.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 共用工具層，集中錯誤、設定、品質與狀態資料庫工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/common/config.py
# Timestamp: 2026-06-08
# Version: v0.2
# Description: 全域環境設定。定義 BASE_DIR、RAG_DB_PATH、QDRANT_URL 等共用路徑常數。
#              所有子模組從此處統一取得設定，禁止各模組自行硬寫路徑。
# ----------------------------------------------------------------------------------------------------

import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
DATA_DIR = os.path.join(BASE_DIR, "data")
SYS_DIR = os.path.join(DATA_DIR, "sys")
DB_DIR = os.path.join(SYS_DIR, "database")
PROCESS_DIR = os.path.join(DATA_DIR, "working", "process")
RAG_DB_PATH = os.path.join(DB_DIR, "rag_state.db")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_TIMEOUT_SECONDS = float(os.environ.get("QDRANT_TIMEOUT_SECONDS", "60"))

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(PROCESS_DIR, exist_ok=True)
