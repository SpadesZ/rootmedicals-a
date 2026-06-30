# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step4_recognizer.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core0 文獻處理層，負責 PDF/文獻前處理與可追溯內容落地。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/ebm-rag/rag_core/core0_literature/lit_pipeline/step4_recognizer.py
# 版本: v0.3
# 更版時間: 2026-05-14 06:10
# 說明: 
#   1. 移除特定專有名詞日誌，統一使用 Recognize。
#   2. 導入 concurrent.futures 實作區塊級別的超時監控 (30秒)。
#   3. 實作 Fallback 機制：遇錯或超時寫入降級字串，防止管線死鎖。
# ----------------------------------------------------------------------------------------------------

import os
import json
import concurrent.futures
import easyocr

os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'

def _recognize_task(reader, img_path):
    """獨立的辨識任務，供執行緒池調用"""
    result = reader.readtext(img_path, detail=0, paragraph=True)
    if result:
        return " ".join(result)
    return ""

def run_recognizer(paper_id: str, ebm_rag_root: str):
    process_dir = os.path.join(ebm_rag_root, "data", "working", "process", paper_id)
    crop_dir = os.path.join(process_dir, "crop")
    meta_dir = os.path.join(process_dir, "metadata")
    recog_dir = os.path.join(process_dir, "recog")
    
    os.makedirs(recog_dir, exist_ok=True)
    progress_file = os.path.join(meta_dir, "recognize_progress.json")

    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump({
            "status": "initializing", 
            "page": 0, 
            "processed": 0, 
            "total": 0,
            "failed": 0
        }, f)

    # 隱藏底層套件名稱
    print(f"[*] Step 4: 啟動 Recognize 引擎 (具備 Timeout Fallback) -> {paper_id}")
    
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)

    manifest_path = os.path.join(meta_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return
        
    with open(manifest_path, "r", encoding="utf-8") as f:
        total_pages = json.load(f).get("total_pages", 0)

    # 建立單一執行緒池處理超時
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    for p in range(1, total_pages + 1):
        layout_path = os.path.join(meta_dir, f"page_{p}_layout.json")
        if not os.path.exists(layout_path): 
            continue

        with open(layout_path, "r", encoding="utf-8") as lf:
            layout = json.load(lf)

        bboxes = layout.get("bboxes", [])
        total_objects = len(bboxes)
        blocks = []
        page_failed_count = 0

        for idx, box_data in enumerate(bboxes):
            
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump({
                    "status": "processing", 
                    "page": p, 
                    "processed": idx + 1, 
                    "total": total_objects,
                    "failed": page_failed_count
                }, f)

            crop_filename = f"{paper_id}_page_{p}_obj_{idx}.png"
            crop_path = os.path.join(crop_dir, crop_filename)
            text_result = ""

            if os.path.exists(crop_path):
                # 提交任務並設定 30 秒強制超時
                future = executor.submit(_recognize_task, reader, crop_path)
                try:
                    text_result = future.result(timeout=30.0)
                except concurrent.futures.TimeoutError:
                    # [Fallback 機制 1] 處理卡死
                    print(f"    [Warning] Recognize 超時 (30s) -> {crop_filename}")
                    text_result = "[Recognition Timeout Fallback]"
                    page_failed_count += 1
                except Exception as e:
                    # [Fallback 機制 2] 處理崩潰
                    print(f"    [Warning] Recognize 錯誤 -> {crop_filename}: {str(e)}")
                    text_result = "[Recognition Error Fallback]"
                    page_failed_count += 1

                blocks.append({
                    "box_id": idx,
                    "label": box_data.get("label", "Text"),
                    "text": text_result,
                    "source_crop": crop_filename
                })

        recog_out = os.path.join(recog_dir, f"{paper_id}_page_{p}-recog.json")
        with open(recog_out, "w", encoding="utf-8") as rf:
            json.dump({"page": p, "blocks": blocks}, rf, indent=4)

    executor.shutdown(wait=False)

    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump({
            "status": "done", 
            "page": total_pages, 
            "processed": 0, 
            "total": 0,
            "failed": 0
        }, f)

    return

if __name__ == "__main__":
    pass