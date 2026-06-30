# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step5_reconstructor.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step5_reconstructor.py
# 版本: v0.1
# 更版時間: 2026-05-10 20:30
# 說明: 
#   1. 跨模組呼叫 app/lava/matching_tasks/ 中的 LLM 任務執行語意重建。
#   2. 產出 page_x_reconstruction.json 修補日誌。
# ----------------------------------------------------------------------------------------------------

import os
import json
import asyncio

def run_reconstructor(paper_id: str, ebm_rag_root: str):
    from lava.matching_tasks.semantic_reconstruct import execute_semantic_reconstruction

    process_dir = os.path.join(ebm_rag_root, "data", "working", "process", paper_id)
    recog_dir = os.path.join(process_dir, "recog")
    meta_dir = os.path.join(process_dir, "metadata")

    print(f"[*] Step 5: 正在呼叫 LLM 進行語意重建 -> {paper_id}")

    recog_files = [f for f in os.listdir(recog_dir) if f.endswith("-recog.json")]

    for rf in recog_files:
        page_num = rf.split("_page_")[1].split("-")[0]
        with open(os.path.join(recog_dir, rf), "r", encoding="utf-8") as f:
            data = json.load(f)

        full_text = " ".join([b.get("text", "") for b in data.get("blocks", [])])
        try:
            result = asyncio.run(execute_semantic_reconstruction(full_text))
        except Exception as e:
            result = {
                "status": "failed",
                "reconstructed_text": full_text,
                "edits": [],
                "model": None,
                "connection_id": None,
                "error": str(e)
            }

        recon_path = os.path.join(meta_dir, f"page_{page_num}_reconstruction.json")
        with open(recon_path, "w", encoding="utf-8") as f:
            json.dump({"page": page_num, "result": result}, f, indent=4, ensure_ascii=False)

    print(f"[✔] Step 5 完成，語意重建日誌已儲存")

if __name__ == "__main__":
    pass