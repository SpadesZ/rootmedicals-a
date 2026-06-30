# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/common/errors.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 共用工具層，集中錯誤、設定、品質與狀態資料庫工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/common/errors.py
# Timestamp: 2026-06-08
# Version: v0.1
# Description: RAG 系統自訂例外類別。
#              UnconfiguredError → LAVA 任務未綁定；UnsupportedProviderError → Provider 不支援指定功能。
# ----------------------------------------------------------------------------------------------------

class RagError(Exception):
    pass

class UnconfiguredError(RagError):
    pass

class UnsupportedProviderError(RagError):
    pass
