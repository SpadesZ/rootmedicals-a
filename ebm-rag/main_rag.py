# 檔案路徑: rootmedicals-a/ebm-rag/main_rag.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: EBM-RAG 服務入口、Docker 設定、文件或依賴描述。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/main_rag.py
# Timestamp: 2026-06-08
# Version: v1.1
# Description: RootMedicals EBM-RAG FastAPI entrypoint.
#              Splits Core0 extraction completion from RAG index readiness for user-facing status.
# Legacy Notes:
#   1. [絕對合規] 極致防禦性展開編碼，代碼行數突破 400 行，徹底服從人機協作三大定律。
#   2. [架構對接] 於 /api/v1/viewer 路由正式引入 lit_collector 模組，完成 Raw Data 物理重組引擎對接。
#   3. [企業級防護] 全面導入詳細的 Docstring、明確的例外處理 (Exception Handling) 與垂直展開字典宣告。
#   4. [狀態繼承] 完整保留 recognize_progress.json 的深層讀取與 Fallback 狀態攔截邏輯。
# ----------------------------------------------------------------------------------------------------

# ==========================================
# 標準函式庫引入區 (Standard Library Imports)
# ==========================================
import os
import uuid
import shutil
import json
import time
from typing import Annotated

# ==========================================
# 第三方套件引入區 (Third-Party Imports)
# ==========================================
from fastapi import FastAPI
from fastapi import Request
from fastapi import File
from fastapi import UploadFile
from fastapi import BackgroundTasks
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ==========================================
# 內部模組引入區 (Internal Module Imports)
# ==========================================
from contextlib import asynccontextmanager

from rag_core.core0_literature.lit_pipeline.lit_orchestrator import main_orchestrator
from rag_core.core0_literature.lit_collector import collect_unified_raw_data
from rag_core.common.state_db import init_db
from lava.llm_model import LLMModel
from lava.api_router import router as lava_router
from rag_core.core5_api.router import router as rag_router


@asynccontextmanager
async def lifespan(app_instance):
    await init_db()
    LLMModel.init_db()
    yield


# ==========================================
# 應用程式與環境變數初始化 (App Initialization)
# ==========================================

app = FastAPI(title="RootMedicals EBM-RAG Control Center", lifespan=lifespan)

app.include_router(lava_router)
app.include_router(rag_router)

# 定義並綁定系統根目錄絕對路徑
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ==========================================
# 靜態資源與實體檔案掛載區 (Static Files Mounting)
# ==========================================

# 1. 掛載前端靜態資源 (CSS, JavaScript, Images)
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")
app.mount(
    "/static", 
    StaticFiles(directory=STATIC_DIR), 
    name="static"
)

# 2. 定義實體輸出目錄 (所有管線解析後的中繼檔案存放區)
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "working", "process")

# 確保輸出目錄在系統啟動時實體存在
if not os.path.exists(OUTPUT_DIR): 
    os.makedirs(OUTPUT_DIR, exist_ok=True)

# 將輸出目錄掛載為靜態路由，支援前端直接透過 URL 讀取裁切影像
app.mount(
    "/outputs", 
    StaticFiles(directory=OUTPUT_DIR), 
    name="outputs"
)

# 3. 初始化 Jinja2 模板引擎
TEMPLATE_DIR = os.path.join(BASE_DIR, "app", "template")
templates = Jinja2Templates(directory=TEMPLATE_DIR)


# ==========================================
# 前端視圖路由 (View Routes)
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def render_rag_dashboard(request: Request):
    """
    渲染前端主操作視圖。
    提供文獻上傳、進度監控與單行狀態列 (Status Strip) 介面。
    """
    return templates.TemplateResponse(
        request=request, 
        name="index.html"
    )

@app.get("/admin", response_class=HTMLResponse)
async def render_rag_admin(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html")

@app.get("/lava", response_class=HTMLResponse)
async def render_lava_setup(request: Request):
    return templates.TemplateResponse(request=request, name="lava_setup.html")


# ==========================================
# API 路由 - 歷史清單與檔案管理 (History & Management APIs)
# ==========================================

@app.get("/api/v1/history")
async def get_history_list():
    """
    讀取硬碟中所有已處理的文獻目錄，提取元數據並計算實際處理耗時。
    最終回傳依時間戳記降冪排序的歷史清單。
    
    Returns:
        List[dict]: 包含 paper_id, filename, timestamp, duration, status 的字典陣列。
    """
    history_list = []
    
    # 檢查物理目錄是否正常存取
    if not os.path.exists(OUTPUT_DIR): 
        return history_list
    
    # 遍歷所有的目錄 ID
    for pid in os.listdir(OUTPUT_DIR):
        
        # 宣告當前專案的路徑
        paper_dir = os.path.join(OUTPUT_DIR, pid)
        meta_dir = os.path.join(paper_dir, "metadata")
        manifest_path = os.path.join(meta_dir, "manifest.json")
        
        # 確保存取對象為目錄，且已產出基礎的 manifest 註冊表
        if os.path.isdir(paper_dir) and os.path.exists(manifest_path):
            
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                    
                    # 擷取基礎資訊
                    start_time = manifest_data.get("timestamp", 0)
                    source_pdf_path = manifest_data.get("source_pdf", "Unknown")
                    filename = os.path.basename(source_pdf_path)
                    
                    # 計算耗時：以最後的 reconstruct 檔案修改時間為基準
                    duration_str = "N/A"
                    recon_path = os.path.join(meta_dir, "page_1_reconstruction.json")
                    
                    if os.path.exists(recon_path):
                        end_time = os.path.getmtime(recon_path)
                        duration_sec = int(end_time - start_time)
                        duration_str = f"{duration_sec}s"
                    
                    # 判定管線狀態：檢查是否發生例外錯誤或 Fallback 中斷
                    pipeline_status = "Success"
                    error_path = os.path.join(meta_dir, "pipeline_error.json")
                    
                    if os.path.exists(error_path):
                        pipeline_status = "Failed"

                    # 垂直展開單筆歷史紀錄的字典宣告
                    history_record = {
                        "paper_id": pid,
                        "filename": filename,
                        "timestamp": start_time,
                        "duration": duration_str,
                        "status": pipeline_status
                    }
                    
                    history_list.append(history_record)
                    
            except Exception as e:
                # 忽略損毀的專案目錄
                print(f"[Warning] Failed to parse history for {pid}: {str(e)}")
                continue
    
    # 根據時間戳記進行降冪排序，確保最新的項目在最上方
    sorted_history = sorted(history_list, key=lambda x: x["timestamp"], reverse=True)
    
    return sorted_history

@app.delete("/api/v1/paper/{paper_id}")
async def delete_paper(paper_id: str):
    """
    [Admin 限定] 執行歷史檔案的物理刪除。
    將移除該 Paper ID 對應的整顆目錄，包含所有的 PNG, JSON 與 Crop 影像。
    
    Args:
        paper_id (str): 欲刪除的文獻唯一識別碼。
    """
    # 定義目標目錄路徑
    target_dir = os.path.join(OUTPUT_DIR, paper_id)
    
    # 檢查目標目錄是否存在
    if os.path.exists(target_dir):
        try:
            # 遞迴移除目錄樹
            shutil.rmtree(target_dir)
            
            # 展開回傳字典
            response_data = {
                "status": "success", 
                "message": f"Paper {paper_id} and all related data removed."
            }
            return response_data
            
        except Exception as e:
            # 處理權限不足或檔案被系統佔用時的錯誤
            raise HTTPException(status_code=500, detail=f"Deletion failed: {str(e)}")
            
    # 若找不到對應的目錄，拋出 404 錯誤
    raise HTTPException(status_code=404, detail="Paper ID not found in system.")


# ==========================================
# API 路由 - 檢視器與上傳管線 (Viewer & Upload APIs)
# ==========================================

@app.get("/api/v1/viewer/{paper_id}")
async def get_paper_extraction_details(paper_id: str):
    """
    [架構升級] 對接 lit_collector，獲取重組後的 Raw Data Payload。
    此 API 供 admin.html 進行雙軌模式 (Visual & Raw JSON) 審閱。
    
    Args:
        paper_id (str): 欲檢視的文獻唯一識別碼。
    """
    try:
        # 呼叫 Rag Core 中的文獻重組引擎
        raw_data_payload = collect_unified_raw_data(
            paper_id=paper_id, 
            ebm_rag_root=BASE_DIR
        )
        
        # 檢查 Collector 回傳的狀態碼
        extraction_status = raw_data_payload.get("extraction_status", "unknown")
        
        if extraction_status == "error_missing_directory":
            raise HTTPException(status_code=404, detail="Paper ID directory not found.")
            
        if extraction_status == "error_missing_manifest":
            raise HTTPException(status_code=400, detail="Paper manifest is missing. Data corrupted.")
            
        return raw_data_payload
        
    except HTTPException as http_exc:
        # 重新拋出已捕捉的 HTTP 錯誤
        raise http_exc
    except Exception as e:
        # 捕捉重組引擎中未預期的嚴重錯誤
        raise HTTPException(status_code=500, detail=f"Data aggregation failed: {str(e)}")

@app.post("/api/v1/upload")
async def upload_medical_pdf(background_tasks: BackgroundTasks, file: Annotated[UploadFile, File()]):
    """
    接收前端上傳的 PDF 實體檔案，安全寫入硬碟工作區，
    並將多模態解析任務 (Orchestrator) 推入背景執行緒。
    """
    # 第一層防護：嚴格驗證副檔名
    original_filename = file.filename
    if not original_filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Strictly only PDF files are allowed.")
    
    # 產生具備機構前綴的唯一任務 ID
    unique_hash = uuid.uuid4().hex[:8].upper()
    paper_id = f"RM_{unique_hash}"
    
    # 組合並建立實體儲存目錄
    save_dir = os.path.join(OUTPUT_DIR, paper_id)
    os.makedirs(save_dir, exist_ok=True)
    
    # 實體檔案串流寫入 (防止大檔案記憶體溢出)
    file_path = os.path.join(save_dir, original_filename)
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File save operation failed: {str(e)}")
    
    # 將核心指揮官推入 FastAPI 的 BackgroundTasks 排程中執行
    from rag_core.common.state_db import upsert_paper
    await upsert_paper(paper_id, original_filename, file_path, status="processing")
    background_tasks.add_task(
        main_orchestrator, 
        paper_id, 
        original_filename
    )
    
    # 立即釋放主執行緒，回傳成功狀態給前端
    response_payload = {
        "status": "success", 
        "paper_id": paper_id, 
        "filename": original_filename
    }
    
    return response_payload


# ==========================================
# API 路由 - 高解析度狀態追蹤引擎 (Status Tracking API)
# ==========================================

@app.get("/api/v1/status/{paper_id}")
async def get_pipeline_status(paper_id: str):
    """
    動態掃描實體物理檔案與 JSON 進度日誌，
    提供高解析度的處理計數器與 Fallback 降級狀態供前端渲染。
    """
    
    # 1. 宣告所有需要掃描的子目錄路徑
    paper_dir = os.path.join(OUTPUT_DIR, paper_id)
    meta_path = os.path.join(paper_dir, "metadata")
    png_path = os.path.join(paper_dir, "png")
    crop_path = os.path.join(paper_dir, "crop")
    recog_path = os.path.join(paper_dir, "recog")
    
    # 2. 宏觀階段布林值判定 (用於前端點亮流程圖大燈號)
    rasterize_done = os.path.exists(os.path.join(meta_path, "manifest.json"))
    layout_done = os.path.exists(os.path.join(meta_path, "page_1_layout.json"))
    
    crop_done = False
    if os.path.exists(crop_path):
        if len(os.listdir(crop_path)) > 0:
            crop_done = True
            
    recognize_done = False
    if os.path.exists(recog_path):
        if len(os.listdir(recog_path)) > 0:
            recognize_done = True
            
    reconstruct_done = os.path.exists(os.path.join(meta_path, "page_1_reconstruction.json"))
    
    # 垂直組合階段狀態字典：Core0 與 RAG index 分離，避免首頁上傳流程等待手動 Index。
    ocr_steps_dict = {
        "rasterize": rasterize_done,
        "layout": layout_done,
        "crop": crop_done,
        "recognize": recognize_done,
        "reconstruct": reconstruct_done
    }

    rag_steps_dict = {
        "chunk": os.path.exists(os.path.join(meta_path, "chunks.json")),
        "embed": False,
        "index": False
    }
    
    # 查詢 embed/index 狀態
    try:
        from rag_core.common import state_db as sdb
        chunks_data = await sdb.get_chunks_for_paper(paper_id)
        if chunks_data:
            embedded = [c for c in chunks_data if c.get("embedding_status") == "embedded"]
            rag_steps_dict["embed"] = len(embedded) > 0 and len(embedded) == len(chunks_data)
            rag_steps_dict["index"] = await sdb.all_chunks_indexed(paper_id)
    except Exception:
        pass

    # 3. 微觀細節計數器初始化 (用於前端動態 Console 日誌)
    details_dict = {
        "total_pages": 0,
        "rasterized_pages": 0,
        "layout_pages": 0,
        "cropped_objects": 0,
        "recognized_pages": 0,
        "recognize_progress": None,
        "error": None
    }
    
    # [掃描] 讀取 Manifest 獲取總頁數
    manifest_file = os.path.join(meta_path, "manifest.json")
    if os.path.exists(manifest_file):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
                details_dict["total_pages"] = manifest_data.get("total_pages", 0)
        except Exception:
            pass

    # [掃描] 計算 Rasterize 實際產出檔案數量
    if os.path.exists(png_path):
        raster_count = 0
        for f in os.listdir(png_path):
            if f.endswith(".png"):
                raster_count += 1
        details_dict["rasterized_pages"] = raster_count
        
    # [掃描] 計算 Layout 實際產出檔案數量
    if os.path.exists(meta_path):
        layout_count = 0
        for f in os.listdir(meta_path):
            if f.endswith("_layout.json"):
                layout_count += 1
        details_dict["layout_pages"] = layout_count
        
    # [掃描] 計算 Crop 實際產出檔案數量
    if os.path.exists(crop_path):
        crop_count = 0
        for f in os.listdir(crop_path):
            if f.endswith(".png"):
                crop_count += 1
        details_dict["cropped_objects"] = crop_count
        
    # [掃描] 計算 Recognize 實際產出檔案數量
    if os.path.exists(recog_path):
        recog_count = 0
        for f in os.listdir(recog_path):
            if f.endswith("-recog.json"):
                recog_count += 1
        details_dict["recognized_pages"] = recog_count

    # 4. [關鍵攔截] 讀取物件級 Recognize 進度與 Fallback 參數
    recog_prog_file = os.path.join(meta_path, "recognize_progress.json")
    if os.path.exists(recog_prog_file):
        try:
            with open(recog_prog_file, "r", encoding="utf-8") as f:
                prog_data = json.load(f)
                details_dict["recognize_progress"] = prog_data
        except Exception:
            pass

    # 5. [錯誤防護] 檢查是否發生管線層級的致命中斷
    error_file = os.path.join(meta_path, "pipeline_error.json")
    if os.path.exists(error_file):
        try:
            with open(error_file, "r", encoding="utf-8") as f:
                err_data = json.load(f)
                details_dict["error"] = err_data.get("error_message", "Unknown Pipeline Error")
        except Exception:
            details_dict["error"] = "Error reading pipeline_error.json"
    
    steps_dict = {}
    steps_dict.update(ocr_steps_dict)
    steps_dict.update(rag_steps_dict)

    # 首頁的背景任務只跑 Core0 extraction；RAG chunk/embed/index 由 Admin RAG Operations 手動執行。
    ocr_complete_flag = all(ocr_steps_dict.values())
    rag_ready_flag = all(rag_steps_dict.values())
    
    # 組裝最終的高解析度狀態 Payload
    final_response = {
        "paper_id": paper_id, 
        "steps": steps_dict,
        "ocr_steps": ocr_steps_dict,
        "rag_steps": rag_steps_dict,
        "details": details_dict,
        "ocr_complete": ocr_complete_flag,
        "rag_ready": rag_ready_flag,
        "is_complete": ocr_complete_flag
    }
    
    return final_response


# ==========================================
# 伺服器啟動進入點 (Server Entry Point)
# ==========================================
if __name__ == "__main__":
    import uvicorn
    # 啟動 ASGI 伺服器，對應 Docker 內部網路設定
    uvicorn.run(app, host="0.0.0.0", port=8000)
