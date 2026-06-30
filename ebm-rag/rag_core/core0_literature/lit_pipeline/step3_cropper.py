# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step3_cropper.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step3_cropper.py
# 版本: v0.1
# 更版時間: 2026-05-10 20:30
# 說明: 
#   1. 實作幾何空間距離聚類，將圖/表與其下方的 Caption 進行綁定。
#   2. 執行 OpenCV 物理裁切，輸出至 crop/ 目錄。
# ----------------------------------------------------------------------------------------------------

import os
import json
import cv2

def run_cropper(paper_id: str, ebm_rag_root: str):
    """執行物理裁切與物件綁定"""
    process_dir = os.path.join(ebm_rag_root, "data", "working", "process", paper_id)
    png_dir = os.path.join(process_dir, "png")
    meta_dir = os.path.join(process_dir, "metadata")
    crop_dir = os.path.join(process_dir, "crop")
    os.makedirs(crop_dir, exist_ok=True)

    print(f"[*] Step 3: 正在執行幾何綁定與裁切 -> {paper_id}")
    
    layout_files = sorted([f for f in os.listdir(meta_dir) if f.startswith("page_") and f.endswith("_layout.json")])
    
    for f in layout_files:
        page_num = f.split("_")[1]
        with open(os.path.join(meta_dir, f), "r", encoding="utf-8") as jf:
            layout = json.load(jf)
        
        img = cv2.imread(os.path.join(png_dir, f"{paper_id}_page_{page_num}.png"))
        bboxes = layout["bboxes"]
        
        # 簡單的 Y 軸聚類邏輯：尋找 Figure 下方的 Caption
        for idx, box in enumerate(bboxes):
            label = box["label"]
            x1, y1, x2, y2 = box["box"]
            
            suffix = f"page_{page_num}_obj_{idx}"
            crop_path = os.path.join(crop_dir, f"{paper_id}_{suffix}.png")
            
            # 若為 Figure/Table，嘗試與下方的 Caption 合併裁切
            if label in ["Figure", "Table"]:
                for other in bboxes:
                    if other["label"] == "Caption":
                        ox1, oy1, ox2, oy2 = other["box"]
                        if 0 < (oy1 - y2) < 50: # 距離 50 像素內
                            y2 = oy2 # 合併高度
                            break
            
            cv2.imwrite(crop_path, img[y1:y2, x1:x2])
            
    print(f"[✔] Step 3 完成，獨立物件已產出至 crop/")

if __name__ == "__main__":
    pass