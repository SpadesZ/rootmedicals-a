# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/lit_orchestrator.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/lit_orchestrator.py
# 版本: v0.4
# 更版時間: 2026-05-13 14:35
# 說明: 
#   1. EBM-RAG 萃取管線總指揮，負責依序調度 5 個獨立階段執行器。
#   2. [重大修復] 移除致死性的 sys.exit(1)，防止背景任務崩潰時連帶殺死 FastAPI 主伺服器行程。
#   3. [功能新增] 導入 traceback 與實體錯誤落盤機制 (pipeline_error.json)，確保錯誤可被追蹤且伺服器持續存活。
#   4. 嚴格確保原有的 5 階段管線調度邏輯完整保留，代碼行數大幅增加以增強容錯性。
# ----------------------------------------------------------------------------------------------------

import os
import sys
import json
import time
import traceback

# 引入階段執行器 (使用相對路徑 '.' 確保 Python 模組正確解析)
from .step1_rasterizer import run_rasterizer
from .step2_layouter import run_layouter
from .step3_cropper import run_cropper
from .step4_recognizer import run_recognizer
from .step5_reconstructor import run_reconstructor

def main_orchestrator(paper_id: str, pdf_filename: str):
    """
    RAG Literature Pipeline 總調度中心
    具備非阻塞容錯防護與實體錯誤落盤機制
    """
    # 定義根路徑
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    ebm_rag_root = os.path.abspath(os.path.join(curr_dir, "../../../"))
    
    # 組合路徑：精準對應 main_rag.py 的儲存結構
    base_process_dir = os.path.join(ebm_rag_root, "data", "working", "process", paper_id)
    pdf_path = os.path.join(base_process_dir, pdf_filename)
    metadata_dir = os.path.join(base_process_dir, "metadata")

    print(f"\n[ORCHESTRATOR] 開始執行管線任務: {paper_id}")
    print("=" * 60)

    try:
        # Step 1: 點陣化 (PDF -> 300 DPI PNG)
        run_rasterizer(paper_id, pdf_path, ebm_rag_root)
        
        # Step 2: 佈局偵測 (YOLOv8 DocLayNet)
        run_layouter(paper_id, ebm_rag_root)
        
        # Step 3: 物理裁切 (幾何 Y 軸聚類與 OpenCV 切割)
        run_cropper(paper_id, ebm_rag_root)
        
        # Step 4: OCR 辨識 (EasyOCR 文本提取)
        run_recognizer(paper_id, ebm_rag_root)
        
        # Step 5: LLM 語意重組 (跨模組語意修補)
        run_reconstructor(paper_id, ebm_rag_root)

        # Step 6: 寫入 state DB
        try:
            from rag_core.core0_literature.persist import persist_core0
            persist_core0(paper_id, ebm_rag_root)
        except Exception as persist_err:
            print(f"[Warning] persist_core0 failed: {persist_err}")

        print("=" * 60)
        print(f"[SUCCESS] Paper {paper_id} 萃取任務全數完成。")

    except Exception as e:
        # 捕捉完整錯誤追蹤訊息
        error_msg = str(e)
        stack_trace = traceback.format_exc()
        
        print(f"\n[CRITICAL ERROR] 管線在執行中發生中斷: {error_msg}")
        print(stack_trace)
        
        # 實作錯誤落盤機制 (Error Manifest 落盤)
        # 確保 metadata 資料夾存在，即使在 Step 1 崩潰前也能寫入
        os.makedirs(metadata_dir, exist_ok=True)
        error_log_path = os.path.join(metadata_dir, "pipeline_error.json")
        
        error_data = {
            "paper_id": paper_id,
            "status": "failed",
            "timestamp": time.time(),
            "error_message": error_msg,
            "stack_trace": stack_trace
        }
        
        with open(error_log_path, "w", encoding="utf-8") as f:
            json.dump(error_data, f, indent=4)
            
        print(f"[INFO] 錯誤報告已實體寫入至: {error_log_path}")
        print(f"[INFO] 已優雅終止任務 {paper_id}，FastAPI 伺服器將繼續保持運行。")
        
        # 使用 return 取代 sys.exit(1)，防止 Uvicorn Worker 被強制刪除
        return

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python lit_orchestrator.py <paper_id> <pdf_filename>")
    else:
        main_orchestrator(sys.argv[1], sys.argv[2])