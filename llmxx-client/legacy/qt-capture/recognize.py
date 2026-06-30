# 路徑: C:\Users\88696\11_projects\rootmedicals-a\llmxx-client\recognize.py
# 版本: v0.2
# 更版時間: 2026-05-04 09:35
# 說明: HIS 系統截圖的 OCR 與版面分析模組 (改用 EasyOCR)
# 依賴: easyocr, numpy, opencv-python

import os
import json
import numpy as np
import easyocr
import cv2
from datetime import datetime
from typing import Dict, List, Any

class HISRecognizer:
    def __init__(self, gpu: bool = False):
        """
        初始化 EasyOCR 辨識器
        :param gpu: 是否啟用 GPU 加速 (若診間電腦無 NVIDIA 顯卡請設為 False)
        """
        print("正在載入 EasyOCR 模型 (首次執行可能需要下載)...")
        # 載入繁體中文與英文模型
        self.reader = easyocr.Reader(['ch_tra', 'en'], gpu=gpu)
        print("模型載入完成。")

    def _identify_titles(self, ocr_results: List[Any]) -> List[Dict[str, Any]]:
        """
        步驟 2: 找 Title
        分析 EasyOCR 回傳的邊界框，基於區塊高度尋找潛在的標題。
        """
        if not ocr_results:
            return []

        analyzed_blocks = []
        heights = []

        # 1. 第一次解析：轉換座標並計算所有區塊的高度
        for bbox, text, prob in ocr_results:
            # bbox 格式為 [[x1, y1], [x2, y1], [x2, y2], [x1, y2]] (左上, 右上, 右下, 左下)
            # 我們將其轉換為 (x, y, w, h) 方便處理
            (tl, tr, br, bl) = bbox
            x = int(tl[0])
            y = int(tl[1])
            w = int(tr[0] - tl[0])
            h = int(bl[1] - tl[1])

            heights.append(h)
            analyzed_blocks.append({
                "bbox": {"x": x, "y": y, "w": w, "h": h},
                "text": text.strip(),
                "confidence": round(float(prob), 4),
                "is_title": False # 預設為 False
            })

        # 2. 第二次解析：判定 Title
        # 計算平均高度 (Mean Height)，字體明顯較大的極可能為標題
        if heights:
            avg_height = sum(heights) / len(heights)
            height_threshold = avg_height * 1.5 # 假設高度大於平均值 1.5 倍的為標題

            for block in analyzed_blocks:
                if block["bbox"]["h"] > height_threshold and block["bbox"]["w"] < 400:
                    block["is_title"] = True

        return analyzed_blocks

    def process_image(self, image_path: str) -> str:
        """
        主流程：讀取 -> 內建切割與辨識 -> 判斷標題 -> 輸出 JSON
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"找不到影像檔案: {image_path}")

        # 使用 OpenCV 讀取影像 (EasyOCR 也接受 cv2 格式的 numpy array)
        img = cv2.imread(image_path)
        
        # 步驟 1 & 3: 切區隔與文字辨識 (由 EasyOCR 的 readtext 一次完成)
        # detail=1 表示回傳完整的 (bbox, text, prob) 結構
        # paragraph=False 保持細粒度的文字區塊，利於後續結構化分析
        raw_results = self.reader.readtext(img, detail=1, paragraph=False)

        # 步驟 2: 找 Title 並整理資料結構
        analyzed_blocks = self._identify_titles(raw_results)

        # 步驟 4: 輸出結構化 JSON
        output_data = {
            "source_image": os.path.basename(image_path),
            "timestamp": datetime.now().isoformat(),
            "extracted_blocks": analyzed_blocks
        }

        return json.dumps(output_data, ensure_ascii=False, indent=2)

# =========================================================================================
# 測試區塊
# =========================================================================================
if __name__ == "__main__":
    # 建立一個測試用的空白圖片來模擬執行 (實務上請換成真實截圖路徑)
    test_img_path = "test_his_capture.png"
    
    # 為了方便您測試，若無圖片則產生一張有字的黑白假圖
    if not os.path.exists(test_img_path):
        print(f"找不到 {test_img_path}，自動產生一張測試圖片...")
        dummy_img = np.ones((400, 600, 3), dtype=np.uint8) * 255
        # 畫個大標題
        cv2.putText(dummy_img, 'Patient Summary', (50, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 2)
        # 畫些內文
        cv2.putText(dummy_img, 'Dx: Achilles tendinopathy', (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 1)
        cv2.putText(dummy_img, 'Tx: Surgery recommended', (50, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 1)
        cv2.imwrite(test_img_path, dummy_img)

    print("開始處理影像...")
    
    # 初始化辨識器 (初次執行會下載模型檔約數十 MB)
    recognizer = HISRecognizer(gpu=False) 
    
    try:
        json_result = recognizer.process_image(test_img_path)
        print("\n=== 辨識結果輸出 (JSON) ===")
        print(json_result)
    except Exception as e:
        print(f"處理失敗: {e}")