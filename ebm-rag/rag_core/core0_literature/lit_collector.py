# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_collector.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_collector.py
# 版本: v0.1
# 更版時間: 2026-05-14 20:50
# 說明: 
#   1. [全新建置] 負責 RAG 管線處理後的碎片化資料重組 (Data Aggregation)。
#   2. 將 Layout 的幾何邊界、Recognize 的文本與 Crop 影像路徑進行精確對位。
#   3. 輸出標準化且結構清晰的 Raw Data Dictionary，供 Admin 審閱或後續 LLM 注入。
#   4. 嚴格採用展開式編碼，確保例外處理與字典宣告的結構絕對清晰。
# ----------------------------------------------------------------------------------------------------

import os
import json

def collect_unified_raw_data(paper_id: str, ebm_rag_root: str) -> dict:
    """
    掃描並聚合指定文獻的多模態解析結果，重組成結構化的 Raw Data 組合。
    
    Args:
        paper_id (str): 文獻的唯一識別碼。
        ebm_rag_root (str): ebm-rag 系統根目錄絕對路徑。
        
    Returns:
        dict: 包含文獻元數據與逐頁重組資料的字典組合。
    """
    
    # 1. 定義基礎物理路徑
    base_process_dir = os.path.join(ebm_rag_root, "data", "working", "process", paper_id)
    metadata_dir = os.path.join(base_process_dir, "metadata")
    recog_dir = os.path.join(base_process_dir, "recog")
    
    # 初始化最終的 Raw Data Payload 結構
    raw_data_payload = {
        "paper_id": paper_id,
        "filename": "Unknown",
        "total_pages": 0,
        "extraction_status": "incomplete",
        "document_stream": []
    }
    
    # 2. 驗證專案目錄與 Manifest 檔案是否存在
    if not os.path.exists(base_process_dir):
        raw_data_payload["extraction_status"] = "error_missing_directory"
        return raw_data_payload
        
    manifest_file_path = os.path.join(metadata_dir, "manifest.json")
    
    if not os.path.exists(manifest_file_path):
        raw_data_payload["extraction_status"] = "error_missing_manifest"
        return raw_data_payload

    # 3. 讀取並提取元數據
    try:
        with open(manifest_file_path, "r", encoding="utf-8") as manifest_f:
            manifest_data = json.load(manifest_f)
            
            source_pdf_path = manifest_data.get("source_pdf", "")
            original_filename = os.path.basename(source_pdf_path)
            total_pages_count = manifest_data.get("total_pages", 0)
            
            raw_data_payload["filename"] = original_filename
            raw_data_payload["total_pages"] = total_pages_count
            raw_data_payload["extraction_status"] = "success"
            
    except Exception as error:
        raw_data_payload["extraction_status"] = f"manifest_parse_error: {str(error)}"
        return raw_data_payload

    # 4. 執行 1-Indexed 跨頁資料精確對位與重組
    current_page = 1
    
    while current_page <= total_pages_count:
        
        # 宣告當前頁面的路徑
        current_layout_path = os.path.join(metadata_dir, f"page_{current_page}_layout.json")
        current_recog_path = os.path.join(recog_dir, f"{paper_id}_page_{current_page}-recog.json")
        
        # 檢查該頁面是否已完成解析
        if os.path.exists(current_layout_path) and os.path.exists(current_recog_path):
            
            try:
                with open(current_layout_path, "r", encoding="utf-8") as layout_f:
                    layout_json = json.load(layout_f)
                    
                with open(current_recog_path, "r", encoding="utf-8") as recog_f:
                    recog_json = json.load(recog_f)
                    
                # 提取該頁面的區塊陣列
                page_bboxes = layout_json.get("bboxes", [])
                page_blocks = recog_json.get("blocks", [])
                
                # 遍歷每一個 OCR 辨識區塊，並與 Layout 幾何資訊進行對位綁定
                block_index = 0
                for block in page_blocks:
                    
                    # 基礎欄位預設值
                    semantic_label = "Text"
                    geometric_box = []
                    confidence_score = 0.0
                    
                    # 確保索引不越界，進行對位
                    if block_index < len(page_bboxes):
                        target_bbox = page_bboxes[block_index]
                        semantic_label = target_bbox.get("label", "Text")
                        geometric_box = target_bbox.get("box", [])
                        confidence_score = target_bbox.get("conf", 0.0)
                    
                    # 解析圖片路徑
                    crop_filename = block.get("source_crop", "")
                    relative_image_url = None
                    
                    if "page_" in crop_filename:
                        relative_image_url = f"/outputs/{paper_id}/crop/{crop_filename}"
                    
                    # 組裝單一物件的 Raw Node 結構
                    raw_node = {
                        "page_number": current_page,
                        "object_id": block_index,
                        "semantic_type": semantic_label,
                        "geometry_bbox": geometric_box,
                        "layout_confidence": confidence_score,
                        "extracted_content": block.get("text", ""),
                        "image_url": relative_image_url
                    }
                    
                    raw_data_payload["document_stream"].append(raw_node)
                    block_index += 1
                    
            except Exception as page_error:
                # 記錄單頁解析錯誤，但不中斷整體迴圈
                print(f"[Warning] Collector error on page {current_page}: {str(page_error)}")
        
        # 推進至下一頁
        current_page += 1

    return raw_data_payload

if __name__ == "__main__":
    pass