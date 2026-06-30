# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step2_layouter.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step2_layouter.py
# 版本: v0.2
# 更版時間: 2026-05-13 16:40
# 說明: 
#   1. [核心升級] 導入 OpenCV 智慧視覺備援機制 (Smart Fallback Heuristics)。
#   2. 當 YOLO 權重缺失時，不再使用全白邊緣的假座標，改以型態學偵測真實文字區塊。
#   3. 適配 1-Indexed 檔名讀取機制。
# ----------------------------------------------------------------------------------------------------

import os
import json
import cv2
import numpy as np
from ultralytics import YOLO

def run_layouter(paper_id: str, ebm_rag_root: str):
    """執行 YOLO 佈局偵測或 OpenCV 視覺備援"""
    process_dir = os.path.join(ebm_rag_root, "data", "working", "process", paper_id)
    png_dir = os.path.join(process_dir, "png")
    meta_dir = os.path.join(process_dir, "metadata")
    
    model_path = os.path.join(ebm_rag_root, "rag_core", "core0_literature", "weights", "yolov8n_doclaynet.pt")
    model = YOLO(model_path) if os.path.exists(model_path) else None

    print(f"[*] Step 2: 正在執行版面分析 -> {paper_id}")
    png_files = sorted([f for f in os.listdir(png_dir) if f.endswith(".png")])
    
    for filename in png_files:
        img_path = os.path.join(png_dir, filename)
        # 動態萃取 1-Indexed 頁碼
        page_num = filename.split("_page_")[1].split(".")[0]
        bboxes = []
        
        if model:
            results = model(img_path)
            for r in results:
                for box in r.boxes:
                    coords = [int(x) for x in box.xyxy[0].tolist()]
                    bboxes.append({
                        "label": model.names[int(box.cls[0])],
                        "box": coords,
                        "conf": float(box.conf[0])
                    })
        else:
            # ==========================================
            # OpenCV 智慧視覺備援機制
            # ==========================================
            print(f"    [Warning] 啟動 OpenCV 備援引擎解析頁面 {page_num}")
            img = cv2.imread(img_path)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                # 二值化反轉
                _, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)
                # 形態學膨脹：將鄰近字元融合成文字段落區塊
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 10))
                dilated = cv2.dilate(thresh, kernel, iterations=3)
                contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for c in contours:
                    x, y, w, h = cv2.boundingRect(c)
                    # 過濾過小雜訊
                    if w > 100 and h > 30:
                        bboxes.append({
                            "label": "Text",
                            "box": [x, y, x+w, y+h],
                            "conf": 0.95
                        })
                # 確保區塊由上往下排列
                bboxes = sorted(bboxes, key=lambda b: b["box"][1])
            
            # 終極防呆：若連輪廓都找不到，才給定整體畫布
            if not bboxes:
                h, w = img.shape[:2] if img is not None else (3508, 2480)
                bboxes = [{"label": "Text", "box": [100, 100, w-100, h-100], "conf": 0.99}]
            
        layout_path = os.path.join(meta_dir, f"page_{page_num}_layout.json")
        with open(layout_path, "w", encoding="utf-8") as f:
            json.dump({"page": int(page_num), "bboxes": bboxes}, f, indent=4)
            
    print(f"[✔] Step 2 完成，已產出 {len(png_files)} 份 Layout JSON")

if __name__ == "__main__":
    pass