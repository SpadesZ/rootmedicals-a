# 檔案路徑: rootmedicals-a/ebm-rag/lava/__init__.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

#路徑(./fyedl/app/llm_service/__init__.py) #版本 v0.1 #更版時間 20251229-1300
from typing import Dict

_bus_instances: Dict[int, 'LlmBus'] = {}

def _get_bus(bus_id: int):
    """
    取得或建立指定 ID 的 LlmBus 實體 (Singleton Pattern)
    """
    # Local import 避免循環依賴
    from .llm_bus import LlmBus  
    
    if bus_id not in _bus_instances:
        bus = LlmBus()
        # 嘗試從 DB 載入設定
        try:
            bus.load_from_config(bus_id)
        except Exception as e:
            print(f"[LlmService] Bus {bus_id} load config failed: {e}")
        _bus_instances[bus_id] = bus
        
    return _bus_instances[bus_id]