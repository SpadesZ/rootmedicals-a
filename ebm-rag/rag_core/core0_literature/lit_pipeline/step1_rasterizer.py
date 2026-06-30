# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step1_rasterizer.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step1_rasterizer.py
# 版本: v0.2
# 更版時間: 2026-05-13 16:40
# 說明: 
#   1. [核心修復] 全面實作 1-Indexed 頁碼編制 (page_num = i + 1)，修正臨床閱讀習慣與後續錯誤。
#   2. 輸出之實體檔案名稱改為 page_1.png 起跳，完美解決 0 頁碼造成的邏輯崩潰。
# ----------------------------------------------------------------------------------------------------

import os
import json
import time
import fitz  # PyMuPDF

def run_rasterizer(paper_id: str, pdf_path: str, ebm_rag_root: str):
    """將 PDF 轉為 PNG 並初始化目錄結構 (1-Indexed)"""
    process_dir = os.path.join(ebm_rag_root, "data", "working", "process", paper_id)
    png_dir = os.path.join(process_dir, "png")
    metadata_dir = os.path.join(process_dir, "metadata")
    
    for d in [png_dir, metadata_dir]:
        os.makedirs(d, exist_ok=True)
        
    print(f"[*] Step 1: 正在點陣化 PDF -> {paper_id}")
    doc = fitz.open(pdf_path)
    zoom = 300 / 72
    mat = fitz.Matrix(zoom, zoom)
    
    page_files = []
    for i in range(len(doc)):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        
        # [修正] 頁碼從 1 開始
        page_num = i + 1
        filename = f"{paper_id}_page_{page_num}.png"
        path = os.path.join(png_dir, filename)
        
        pix.save(path)
        page_files.append({"page_num": page_num, "png_path": path})
        
    manifest = {
        "paper_id": paper_id,
        "source_pdf": pdf_path,
        "total_pages": len(doc),
        "status": "rasterized",
        "timestamp": time.time(),
        "pages": page_files
    }
    
    manifest_path = os.path.join(metadata_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4)
    
    print(f"[✔] Step 1 完成，產出 {len(doc)} 張影像與 manifest.json (頁碼 1 至 {len(doc)})")
    return manifest_path

if __name__ == "__main__":
    pass