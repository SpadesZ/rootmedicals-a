# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/persist.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core0_literature/persist.py
# Timestamp: 2026-06-08
# Version: v0.1
# Description: Core0 OCR 輸出持久化層。
#              orchestrator 完成 Step5 後呼叫 persist_core0()，
#              將 lit_collector 的 raw_data 寫入 papers / ocr_blocks 表並記錄 pipeline_event。
# ----------------------------------------------------------------------------------------------------

import asyncio
from rag_core.common import state_db as sdb
from rag_core.core0_literature.lit_collector import collect_unified_raw_data

def persist_core0(paper_id: str, ebm_rag_root: str):
    raw_data = collect_unified_raw_data(paper_id, ebm_rag_root)
    asyncio.run(_async_persist(paper_id, raw_data))

async def _async_persist(paper_id: str, raw_data: dict):
    total_pages = raw_data.get("total_pages", 0)
    filename = raw_data.get("filename", "")
    await sdb.update_paper_ocr(paper_id, raw_data, status="core0_done")
    await sdb.log_event(paper_id, "core0", "completed", f"OCR done: {total_pages} pages, {len(raw_data.get('document_stream', []))} blocks")
