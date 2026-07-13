# 檔案路徑: rootmedicals-a/llmebm/main_ebm.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmebm 長期 EBM 知識層、taxonomy UI 或容器設定。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/llmebm/main_ebm.py
# 版本: v1.7
# 更版時間: 2026-07-13 (analyze-soap 靜態假內容停用)
# 說明: 
#   1. 嚴格遵守人機協作第一定律：代碼行數只增不減，全量展開所有註解與文檔字串 (Docstrings)。
#   2. 完整保留 v0.6 的所有基礎路由 (/admin, /, /api/health) 與錯誤攔截器。
#   3. [v1.7 停用] /api/v1/analyze-soap + pipeline_event_generator 原本串流「寫死的 AAA
#      監測綠燈/evidence 1a/假 DOI」假臨床結果（與真實 RAG 無關）。為符合能賣等級「來源
#      可追、不許假內容」硬 gate 已停用：端點保留避免 index.html 舊按鈕 404，但不再輸出
#      任何臨床判斷或紅綠燈。動態實證內容改由 Topic Page (/topic/...) 逐 slot 生成。
#   4. 疊加 SQLite 資料庫模組 (ebm_model.py) 整合，確保持久化資料庫連線。
#   5. [更新] /api/v1/specialties 與 /api/v1/specialties/{specialty_name}/tree 路由改呼叫 v2 函式以支援5層架構。
#   6. 保留 /api/v1/settings 路由 (GET/POST)：供前端 Admin 讀寫全域品牌設定。
#   7. [新增] /api/v1/admin/taxonomy 與 /api/v1/admin/taxonomy/upload 路由，支援 CSV 批次上傳與層級聯動。
#   8. [v1.0 新增] 擴充 NodeCreate / NodeUpdate 模型與手動增刪改 CRUD 路由。
#   9. [v1.2 疊加] 前台 API 切換至 admin_db 資料源，解決資料脫鉤斷層。
#  10. [v1.3 修復] 移除 /tree 路由中引發 404 報錯的舊防呆機制，解決空節點導致前端崩潰的問題。
#  11. [v1.4 擴充] 升級 create_taxonomy_node 路由，支援逗號分隔字串的自動解析與批次寫入。
#  12. [v1.5 終極修復] 絕對全量展開代碼！新增 /topic/{topic_name} 路由以渲染 specialty.html，並動態傳遞階層 UID。
#  13. [v1.6 最終確認] 100% 繼承所有歷史代碼，於底部包含 Sidebar Topic Builder 雙畫布所需的 flat, POST, DELETE API 路由。
# ----------------------------------------------------------------------------------------------------

import os
import json
import asyncio
import urllib.error
import urllib.request
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional  # [v1.0 擴充] 新增 Optional 供手動 CRUD 模型使用
import uvicorn

# [擴充] 引入上傳檔案與表單處理所需的模組，絕對不刪改上方原生 import
from fastapi import UploadFile, File, Form

# [修正] 引入剛寫好的 SQLite 模型 (對齊 app.model 架構)
from app.model.ebm_model import ebm_db
from app.model.ebm_model import admin_db
# [擴充] 引入獨立的 Sidebar 知識庫引擎供 specialty.html 使用
from app.model.sidebar_model import sidebar_db
from app.model.topic_content_model import build_manifest, load_screenshot_payloads, topic_content_db
from app.topic_security import generation_request_authorized

# 建立 FastAPI 實例
app = FastAPI(
    title="RootMedicals llmebm",
    description="實證醫學與病歷知識庫 (RAG Container) - 支援 SSE 極速串流與 SQLite 三層架構",
    version="1.8"
)

# 允許前端跨域請求 (確保 SSE 串流與外部 API 呼叫不受 CORS 限制)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 定義 Pydantic 模型供設定寫入驗證使用
class SystemSettingsModel(BaseModel):
    brand_name: str
    brand_logo: str = ""

# ==========================================
# [v1.0 擴充] CRUD 資料驗證模型
# ==========================================
class NodeCreate(BaseModel):
    parent_id: Optional[int] = None
    name: str

class NodeUpdate(BaseModel):
    name: str

# [v1.11 新增] Sidebar 客製化節點驗證模型
class SidebarNodeCreate(BaseModel):
    parent_id: Optional[int] = None
    name: str

class TopicGenerateRequest(BaseModel):
    only_slot_ids: list[str] = Field(default_factory=list)
    force: bool = False
    filters: dict = Field(default_factory=dict)
    top_k: int = 10

# 取得當前目錄的絕對路徑，確保 Docker 內外路徑解析正確
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "app", "templates")

# 確保目錄存在，避免掛載時引發啟動錯誤，確保架構完整性
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "images"), exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# 掛載靜態檔案 (CSS, JS, 圖片)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 初始化 Jinja2 模板引擎
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ==========================================
# 系統容錯與例外捕捉處理器 (Exception Handlers)
# ==========================================
@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc: Exception):
    """
    全域 404 錯誤處理器，提供友善的 JSON 除錯提示
    """
    return JSONResponse(
        status_code=404,
        content={"message": f"找不到請求的資源: {request.url.path}", "error_code": 404}
    )

@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc: Exception):
    """
    全域 500 錯誤處理器，攔截未預期崩潰並回傳詳細結構
    """
    return JSONResponse(
        status_code=500,
        content={
            "message": "伺服器內部發生錯誤", 
            "detail": str(exc),
            "path": request.url.path
        }
    )

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    攔截瀏覽器預設的 favicon 請求，回傳 204 No Content，徹底消除終端機的 404 日誌洗版
    """
    return Response(status_code=204)

# ==========================================
# EBMDx 核心業務路由 (原版保留)
# ==========================================
@app.get("/")
async def render_ebm_workspace(request: Request):
    """
    渲染 RootMedicals-EBMDx 風格的實證醫學前端工作頁面
    """
    template_file = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.exists(template_file):
        return {"message": "主模板尚未建立，請至 app/templates/index.html 進行開發。"}
    
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"request": request}
    )

@app.get("/admin")
async def render_admin_page(request: Request):
    """
    渲染系統後台管理頁面 (供修改 Logo 與 Brand Name)
    """
    template_file = os.path.join(TEMPLATES_DIR, "admin.html")
    if not os.path.exists(template_file):
        return {"message": "Admin 模板尚未建立，請至 app/templates/admin.html 進行開發。"}
    
    return templates.TemplateResponse(
        request=request, 
        name="admin.html", 
        context={"request": request}
    )

# =========================================================================
# [v1.8 全新擴充] Specialty/Topic 獨立內容頁面與左側選單 API
# =========================================================================
@app.get("/topic/{topic_name}")
async def render_specialty_page(request: Request, topic_name: str):
    """
    渲染三視窗 (Sidebar + Content + Assets) 的獨立疾病頁面。
    動態呼叫 admin_db 撈取該節點專屬的階層 Base-33 UID，傳遞給前端標題顯示。
    """
    node_info = admin_db.get_node_by_name(topic_name)
    topic_uid = sidebar_db.topic_uid_for(topic_name, node_info.get('uid', '') if node_info else '')
    
    return templates.TemplateResponse(
        request=request, 
        name="specialty.html", 
        context={
            "request": request, 
            "topic_name": topic_name,
            "topic_uid": topic_uid
        }
    )

@app.get("/api/v1/topic/{topic_name}/sidebar")
async def get_topic_sidebar(topic_name: str):
    """動態撈取特定主題的 3 層 Sidebar 目錄 (包含通用與自建)"""
    topic_uid = _topic_uid(topic_name)
    tree = sidebar_db.get_sidebar_tree(topic_name, topic_uid)
    current_manifest = topic_content_db.get_current_manifest(topic_name)
    statuses = topic_content_db.status_for_topic(topic_uid, current_manifest)["slots"]

    def add_status(nodes):
        for node in nodes:
            node["content_status"] = statuses.get(node["slot_id"], {}).get("status", "empty")
            add_status(node.get("children", []))

    add_status(tree["universal"])
    add_status(tree["custom"])
    return {"status": "success", "topic": topic_name, "topic_uid": topic_uid, "tree": tree}


def _topic_uid(topic_name: str) -> str:
    node_info = admin_db.get_node_by_name(topic_name)
    return sidebar_db.topic_uid_for(topic_name, node_info.get("uid", "") if node_info else "")


def _ensure_topic_manifest(topic_name: str):
    manifest = topic_content_db.get_current_manifest(topic_name)
    if manifest:
        return manifest
    topic_uid = _topic_uid(topic_name)
    tree = sidebar_db.get_sidebar_tree(topic_name, topic_uid)
    return topic_content_db.save_manifest(build_manifest(topic_name, topic_uid, tree))


def _mark_topic_manifest_dirty(topic_name: str):
    """Rebuild the DOM contract after sidebar edits; the scanner restores screenshots."""
    topic_uid = _topic_uid(topic_name)
    tree = sidebar_db.get_sidebar_tree(topic_name, topic_uid)
    return topic_content_db.save_manifest(build_manifest(topic_name, topic_uid, tree))


def _public_topic_manifest(manifest):
    public_manifest = json.loads(json.dumps(manifest))
    public_manifest["screenshots"] = [
        {key: value for key, value in screenshot.items() if key != "artifact_path"}
        for screenshot in public_manifest.get("screenshots", [])
    ]
    return public_manifest


def _post_topic_generation(payload):
    base_url = os.getenv("EBM_RAG_BASE_URL", "http://127.0.0.1:33301").rstrip("/")
    url = f"{base_url}/api/v1/rag/topic-content/generate"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    generation_token = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "")
    if generation_token:
        headers["X-LLMEBM-Topic-Token"] = generation_token
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read(10 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"ebm-rag returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ebm-rag unavailable: {exc.reason}") from exc
    if len(body) > 10 * 1024 * 1024:
        raise RuntimeError("ebm-rag response exceeded 10 MiB.")
    return json.loads(body.decode("utf-8"))


async def _run_topic_generation(job_id, manifest, screenshots, slot_ids, filters, top_k):
    # ponytail: process-local jobs only support one llmebm instance; move to a durable queue before multi-instance deployment.
    topic_content_db.update_job(job_id, "running")
    try:
        public_manifest = _public_topic_manifest(manifest)
        response = await asyncio.to_thread(
            _post_topic_generation,
            {
                "manifest": public_manifest,
                "screenshots": screenshots,
                "only_slot_ids": slot_ids,
                "filters": filters,
                "top_k": top_k,
            },
        )
        if not isinstance(response, dict) or not isinstance(response.get("sections"), list):
            raise RuntimeError("ebm-rag returned an invalid Topic content response.")
        if response.get("manifest_hash") != manifest["dom_hash"]:
            raise RuntimeError("ebm-rag response manifest hash did not match the current request.")
        plan = response.get("plan")
        requested_slots = set(slot_ids)
        returned_slots = set()
        local_errors = []
        for section in response["sections"]:
            if not isinstance(section, dict) or section.get("slot_id") not in requested_slots:
                local_errors.append({"status": "invalid_response", "message": "ebm-rag returned an unrequested slot."})
                continue
            returned_slots.add(section["slot_id"])
            try:
                topic_content_db.save_section_result(
                    manifest["topic_uid"], manifest["dom_hash"], section, plan=plan
                )
            except (TypeError, ValueError, KeyError) as exc:
                failed = {"slot_id": section.get("slot_id", "unknown"), "status": "failed", "error": str(exc)}
                topic_content_db.save_section_result(manifest["topic_uid"], manifest["dom_hash"], failed, plan=plan)
                local_errors.append({"slot_id": section["slot_id"], "status": "invalid_response", "message": str(exc)})
        for missing_slot in requested_slots - returned_slots:
            local_errors.append({"slot_id": missing_slot, "status": "missing_response"})
        response_errors = [*(response.get("errors") or []), *local_errors]
        topic_content_db.update_job(job_id, "completed_with_errors" if response_errors else "completed", response_errors)
    except Exception as exc:
        topic_content_db.update_job(job_id, "failed", {"message": str(exc)})


@app.get("/api/v1/topic/{topic_name}/manifest")
async def get_topic_manifest(topic_name: str):
    return {"status": "success", "manifest": _public_topic_manifest(_ensure_topic_manifest(topic_name))}


@app.get("/api/v1/topic/{topic_name}/content/status")
async def get_topic_content_status(topic_name: str):
    manifest = _ensure_topic_manifest(topic_name)
    return {
        "status": "success",
        "topic_uid": manifest["topic_uid"],
        **topic_content_db.status_for_topic(manifest["topic_uid"], manifest),
    }


@app.get("/api/v1/topic/{topic_name}/content/{slot_id}")
async def get_topic_content(topic_name: str, slot_id: str):
    manifest = _ensure_topic_manifest(topic_name)
    allowed = {slot["slot_id"] for slot in manifest["slots"]}
    if slot_id not in allowed:
        raise HTTPException(status_code=404, detail="Unknown content slot.")
    result = topic_content_db.get_current_content(manifest["topic_uid"], slot_id)
    if not result:
        return JSONResponse(status_code=404, content={"status": "empty", "slot_id": slot_id})
    return {"slot_id": slot_id, **result}


@app.post("/api/v1/topic/{topic_name}/content/generate")
async def generate_topic_content(topic_name: str, payload: TopicGenerateRequest, http_request: Request):
    if not generation_request_authorized(
        http_request.client.host if http_request.client else "",
        http_request.headers.get("X-LLMEBM-Topic-Token"),
    ):
        raise HTTPException(status_code=403, detail="Topic generation is not authorized.")
    manifest = _ensure_topic_manifest(topic_name)
    if payload.top_k < 1 or payload.top_k > 25:
        raise HTTPException(status_code=400, detail="top_k must be between 1 and 25.")
    allowed = {slot["slot_id"] for slot in manifest["slots"] if slot.get("content_target", True)}
    if payload.only_slot_ids:
        unknown = set(payload.only_slot_ids) - allowed
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown slot IDs: {sorted(unknown)}")
        slot_ids = list(dict.fromkeys(payload.only_slot_ids))
    else:
        statuses = topic_content_db.status_for_topic(manifest["topic_uid"], manifest)["slots"]
        slot_ids = [
            slot["slot_id"] for slot in manifest["slots"]
            if payload.force or statuses[slot["slot_id"]]["status"] in {"empty", "stale", "failed"}
        ]
    if not slot_ids:
        return {"status": "unchanged", "message": "No empty, stale, or failed slots require generation."}
    try:
        screenshots = load_screenshot_payloads(manifest)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job_id = topic_content_db.create_job(manifest["topic_uid"], manifest["dom_hash"], slot_ids)
    asyncio.create_task(_run_topic_generation(job_id, manifest, screenshots, slot_ids, payload.filters, payload.top_k))
    return JSONResponse(status_code=202, content={"status": "accepted", "job_id": job_id, "slot_ids": slot_ids})

@app.get("/api/health")
async def health_check():
    """
    模組健康度與架構檢查 API
    """
    return {
        "status": "ok", 
        "module": "llmebm", 
        "architecture": "lava DDD namespace",
        "entrypoint": "main_ebm.py"
    }

# ==========================================
# Admin 全域設定 API 區塊 (新增)
# ==========================================
@app.get("/api/v1/settings")
async def get_system_settings():
    """
    取得系統全域品牌設定 (Brandname 與 Logo)
    供前端自動渲染雙軌品牌狀態。
    """
    settings = ebm_db.get_system_settings()
    return {"status": "success", "data": settings}

@app.post("/api/v1/settings")
async def update_system_settings(settings: SystemSettingsModel):
    """
    更新系統全域品牌設定，寫入 SQLite 資料庫持久化儲存。
    """
    ebm_db.update_system_settings(settings.brand_name, settings.brand_logo)
    return {"status": "success", "message": "Settings updated successfully."}

# ==========================================
# Specialties API 區塊 (Layer 1, 2, 3 知識庫結構)
# ==========================================
@app.get("/api/v1/specialties")
async def get_specialties():
    """
    取得 Layer 1 科系總表
    此 API 提供前端 / 首頁 3 欄式 (3-column) 總表的資料來源
    [v1.2 疊加] 切換為 admin_db 以讀取所有 46 個專科。
    """
    return {
        "status": "success", 
        "data": admin_db.get_all_specialties_for_frontend()
    }

@app.get("/api/v1/specialties/{specialty_name}/tree")
async def get_specialty_tree(specialty_name: str):
    """
    取得特定科系的 Layer 2 + 3 樹狀結構
    此 API 供前端動態展開 2 欄式瀑布流 (column-count: 2) 結構使用
    [v1.2 疊加] 切換為 admin_db 以讀取 5 層無限遞迴架構。
    """
    tree = admin_db.get_specialty_tree_for_frontend(specialty_name)
    
    # [v1.3 修復] 移除 404 報錯防呆。允許回傳空陣列 []，
    # 交由前端 index.html 進行優雅的無資料渲染，避免觸發 catch 導致紅字系統警告脫鉤。
    # if not tree:
    #     raise HTTPException(
    #         status_code=404, 
    #         detail="Specialty not found or has no content yet."
    #     )
        
    return {
        "status": "success", 
        "specialty": specialty_name, 
        "tree": tree
    }

# ==========================================
# EBMDx SSE 串流引擎 (保留區塊)
# ==========================================
async def pipeline_event_generator(soap_text: str):
    """
    核心串流生成器：精確模擬 llmxx-server 與 RAG 的非同步交火過程
    確保 TTFT (Time-To-First-Token) 壓在 1.5 秒內。
    """
    # 階段 1: 查詢重構與提純
    yield f"data: {json.dumps({'status': 'processing', 'message': 'Extracting clinical keywords and generating embeddings...'})}\n\n"
    await asyncio.sleep(0.5)
    
    # 階段 2: 獨立 RAG 系統混合檢索
    yield f"data: {json.dumps({'status': 'processing', 'message': 'Querying Qdrant Vector DB for top 6S evidence...'})}\n\n"
    await asyncio.sleep(0.8)
    
    # 階段 3: vLLM 推理引擎啟動，即時串流推播 Token
    yield f"data: {json.dumps({'status': 'generating', 'message': 'Applying GRADE criteria and synthesizing recommendations...'})}\n\n"
    await asyncio.sleep(0.2)
    
    reasoning_text = (
        "This static demo stream has been retired for sellable-grade rigor. "
        "It no longer emits any clinical assessment or traffic-light result. "
        "Evidence-backed content is now generated dynamically per topic section "
        "on the Topic Page, with every claim traceable to a retrieved source."
    )
    
    for word in reasoning_text.split():
        yield f"data: {json.dumps({'status': 'streaming', 'token': word + ' '})}\n\n"
        await asyncio.sleep(0.08)
        
    # 已停用 (2026-07-13): 不再輸出寫死的臨床判斷、紅綠燈、evidence level 或假 DOI；
    # 只回明確的停用狀態並導向動態 Topic Page。完整移除需一併拿掉 index.html 的
    # #btn-analyze-soap 與 stream_client.js 的呼叫（列為後續小尾巴）。
    final_payload = {
        "status": "removed",
        "message": "Retired static demo path. Use the dynamic Topic Page for evidence-backed content.",
    }
    yield f"data: {json.dumps(final_payload)}\n\n"

@app.get("/api/v1/analyze-soap")
async def analyze_soap_endpoint(query: str):
    """
    接收前端送來的 SOAP 參數，開啟 SSE 串流通道
    """
    return StreamingResponse(
        pipeline_event_generator(query), 
        media_type="text/event-stream"
    )

# =========================================================================
# Admin Taxonomy Builder 專屬路由
# =========================================================================
@app.get("/api/v1/admin/taxonomy")
async def get_admin_taxonomy(parent_id: int = None):
    """
    提供 Admin 介面動態聯動選單資料 (Cascading Dropdowns)。
    若 parent_id 為 null，則回傳 Layer 1 的基礎科系清單。
    """
    try:
        nodes = admin_db.get_nodes_by_parent(parent_id)
        return {"status": "success", "data": nodes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/admin/taxonomy/upload")
async def upload_admin_taxonomy_csv(
    file: UploadFile = File(...), 
    parent_id: str = Form("null")
):
    """
    接收 Admin 介面上傳的 CSV 檔案與其從屬的 parent_id，
    透過模型將資料批次寫入 specialty.db 的 taxonomy_nodes 資料表中。
    """
    try:
        content = await file.read()
        csv_text = content.decode('utf-8')
        
        # 處理 null 字串的防呆邏輯
        p_id = None if parent_id == "null" or not parent_id.isdigit() else int(parent_id)
        
        inserted = admin_db.batch_insert_from_csv(p_id, csv_text)
        return {"status": "success", "message": f"Successfully imported {inserted} nodes from CSV."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =========================================================================
# 單筆手動 CRUD API 路由
# =========================================================================
@app.post("/api/v1/admin/taxonomy/node")
async def create_taxonomy_node(node: NodeCreate):
    """
    單筆或多筆新增分類節點 (支援逗號分隔)
    [v1.4 擴充] 系統自動解析逗號，將字串拆分為多筆獨立資料寫入資料庫
    """
    try:
        # 支援全形或半形逗號解析
        raw_names = node.name.replace("，", ",").split(",")
        names = [n.strip() for n in raw_names if n.strip()]
        
        if not names:
            raise ValueError("No valid node names provided.")
        
        first_id = None
        for n in names:
            new_id = admin_db.add_node(node.parent_id, n)
            if first_id is None:
                first_id = new_id
                
        # 回傳第一筆新增的 ID 供前端自動選取，並動態提示新增的總筆數
        return {
            "status": "success", 
            "message": f"Successfully added {len(names)} node(s).", 
            "node_id": first_id
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/v1/admin/taxonomy/node/{node_id}")
async def update_taxonomy_node(node_id: int, node: NodeUpdate):
    """單筆修改分類節點名稱"""
    try:
        admin_db.update_node(node_id, node.name)
        return {"status": "success", "message": f"Node updated to '{node.name}'."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/v1/admin/taxonomy/node/{node_id}")
async def delete_taxonomy_node(node_id: int):
    """單筆刪除分類節點"""
    try:
        admin_db.delete_node(node_id)
        return {"status": "success", "message": "Node deleted successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# =========================================================================
# [v1.12 新增疊加] Sidebar Topic Builder 專屬 API (支援雙畫布增刪改查)
# =========================================================================
@app.get("/api/v1/topic/{topic_name}/sidebar/flat")
async def get_topic_sidebar_flat(topic_name: str):
    """撈取供下拉選單用的 L1 & L2 客製化節點清單"""
    nodes = sidebar_db.get_custom_nodes_flat(topic_name)
    return {"status": "success", "data": nodes}

@app.post("/api/v1/topic/{topic_name}/sidebar/node")
async def create_sidebar_node(topic_name: str, node: SidebarNodeCreate):
    """新增 Specialized Features 節點"""
    try:
        new_id = sidebar_db.add_custom_node(topic_name, node.parent_id, node.name)
        manifest = _mark_topic_manifest_dirty(topic_name)
        return {
            "status": "success", "message": "Node added.", "node_id": new_id,
            "scan_required": True, "dom_hash": manifest["dom_hash"],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/v1/topic/sidebar/node/{node_id}")
async def delete_sidebar_node(node_id: int):
    """刪除 Specialized Features 節點"""
    try:
        topic_name = sidebar_db.delete_custom_node(node_id)
        manifest = _mark_topic_manifest_dirty(topic_name)
        return {
            "status": "success", "message": "Node deleted.",
            "scan_required": True, "dom_hash": manifest["dom_hash"],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main_ebm:app", host="0.0.0.0", port=8000, reload=True)
