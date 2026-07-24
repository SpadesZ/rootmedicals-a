# 模組定位: llmebm FastAPI composition root，提供 taxonomy UI 與動態 Topic Page API。
# 主要責任: 渲染頁面、建立 DOM manifest、協調 ebm-rag 生成/mapping review、保存逐 slot 結果、dev review session 與唯讀 Medpilot 問答。
# 呼叫來源: 瀏覽器頁面、Topic 掃描工具、受權的本機或 token 化內容生成請求，以及首頁 Medpilot 單輪查詢。
# 輸入契約: canonical topic slug、manifest slot IDs、驗證過的截圖 artifact 與 TopicGenerateRequest。
# 輸出契約: 靜態 heading + component JSON 內容；舊 analyze-soap 永久回 410，不提供模擬臨床結果。
# 安全邊界: 生成端點限 operator/reviewer token；dev review cookie 僅 local/dev、HttpOnly、SameSite 且仍做 token compare。
# 維護提醒: scoped revision 查詢失敗才可退回 global fail-safe；本機 job 多 instance 前換 durable queue。
# ----------------------------------------------------------------------------------------------------

import os
import json
import asyncio
import urllib.error
import urllib.parse
import urllib.request
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import Optional  # [v1.0 擴充] 新增 Optional 供手動 CRUD 模型使用
import uvicorn

# [擴充] 引入上傳檔案與表單處理所需的模組，絕對不刪改上方原生 import
from fastapi import UploadFile, File, Form

# [修正] 引入剛寫好的 SQLite 模型 (對齊 app.model 架構)
from app.model.ebm_model import ebm_db
from app.model.ebm_model import admin_db
# [擴充] 引入獨立的 Sidebar 知識庫引擎供 specialty.html 使用
from app.model.sidebar_model import canonical_topic_slug, sidebar_db
from app.model.topic_content_model import (
    affected_manifest_slot_ids,
    build_manifest,
    content_paper_ids,
    load_screenshot_payloads,
    manifest_structure_revisions,
    topic_content_db,
    validate_evidence_revision,
)
from app.medpilot import build_medpilot_response, smalltalk_response
from app.topic_security import (
    development_review_session_enabled,
    generation_request_authorized,
    hierarchy_admin_request_authorized,
    mutation_rate_allowed,
    review_request_authorized,
    validate_review_generation_payload,
    validate_review_generation_status,
)

REVIEW_SESSION_COOKIE_NAME = "llmebm_review_session"

# 建立 FastAPI 實例
app = FastAPI(
    title="RootMedicals llmebm",
    description="實證醫學與病歷知識庫 (RAG Container) - 動態 Topic Page 與 SQLite 三層架構",
    version="1.8"
)

# 允許前端跨域請求 (確保 SSE 串流與外部 API 呼叫不受 CORS 限制)
_cors_origins = [
    value.strip() for value in os.getenv(
        "LLMEBM_CORS_ORIGINS", "http://127.0.0.1:33300,http://localhost:33300"
    ).split(",") if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bound_mutation_rate(request: Request, call_next):
    """Apply a cheap local abuse ceiling before token checks and expensive generation work."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        client = request.client.host if request.client else "unknown"
        if not mutation_rate_allowed(f"{client}:{request.url.path}"):
            return JSONResponse(
                status_code=429,
                content={"status": "rate_limited", "message": "Too many mutation requests."},
                headers={"Retry-After": "60"},
            )
    return await call_next(request)

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
    only_slot_ids: list[str] = Field(default_factory=list, max_length=200)
    force: bool = False
    require_current_scope_review: bool = True
    filters: dict = Field(default_factory=dict)
    top_k: int = Field(default=10, ge=1, le=25)

    @field_validator("only_slot_ids")
    @classmethod
    def validate_generation_slot_ids(cls, value):
        normalized = [str(item or "").strip() for item in value]
        if any(not item or len(item) > 300 for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("only_slot_ids must contain unique non-empty IDs up to 300 characters.")
        return normalized

    @field_validator("filters")
    @classmethod
    def validate_generation_filters(cls, value):
        allowed = {
            "specialty", "disease", "source_type", "is_guideline", "has_contraindication_terms",
            "min_ocebm", "min_ocebm_level", "prefer_six_s_levels", "query_decomposition_mode",
        }
        if not isinstance(value, dict) or set(value).difference(allowed):
            raise ValueError("filters contains unsupported fields.")
        if len(json.dumps(value, ensure_ascii=False)) > 20_000:
            raise ValueError("filters payload is too large.")
        return value

class TopicUserStateRequest(BaseModel):
    followed: Optional[bool] = None
    mark_seen: bool = False
    seen_status_hash: Optional[str] = None


class MedpilotQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value):
        query = str(value or "").strip()
        if not query or "\x00" in query:
            raise ValueError("query must contain visible text and no null bytes")
        return query


class TopicReviewRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=2000)


class TopicSourceDetailsRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("paper_ids")
    @classmethod
    def validate_paper_ids(cls, value):
        normalized = [str(item or "").strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("paper_ids entries must be non-empty and at most 200 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("paper_ids contains duplicates")
        return normalized


class TopicReviewDecisionRequest(TopicReviewRequest):
    decision: str


class TopicRollbackRequest(TopicReviewRequest):
    version_id: int = Field(gt=0)


class HierarchyPreviewRequest(BaseModel):
    source: str
    node_key: str
    change: dict


class HierarchyApplyRequest(HierarchyPreviewRequest):
    actor: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=2000)
    expected_before_hash: str


class HierarchyRollbackRequest(BaseModel):
    audit_id: int = Field(gt=0)
    actor: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=2000)

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
    
    dev_review_session = development_review_session_enabled(hostname=request.url.hostname)
    response = templates.TemplateResponse(
        request=request, 
        name="admin.html", 
        context={"request": request, "dev_review_session": dev_review_session}
    )
    if dev_review_session:
        # ponytail: local/dev reuses the configured review secret as an HttpOnly cookie; formal RBAC replaces this path.
        response.set_cookie(
            key=REVIEW_SESSION_COOKIE_NAME,
            value=os.getenv("LLMEBM_REVIEW_ADMIN_TOKEN", ""),
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/api/v1/topic",
        )
    return response

# =========================================================================
# [v1.8 全新擴充] Specialty/Topic 獨立內容頁面與左側選單 API
# =========================================================================
@app.get("/topic/{topic_name}")
async def render_specialty_page(request: Request, topic_name: str):
    """
    渲染三視窗 (Sidebar + Content + Assets) 的獨立疾病頁面。
    topic identity 先 canonicalize，再以 deterministic UID 傳遞給前端與內容版本層。
    """
    canonical_name = canonical_topic_slug(topic_name)
    if canonical_name != topic_name:
        return RedirectResponse(
            url=f"/topic/{urllib.parse.quote(canonical_name)}",
            status_code=308,
        )
    topic_uid = _topic_uid(canonical_name)
    # stable slug/UID 留在資料契約；H1 僅呈現臨床閱讀用名稱，避免洩漏內部識別碼。
    topic_display_name = canonical_name.replace("-", " ").title()
    
    return templates.TemplateResponse(
        request=request, 
        name="specialty.html", 
        context={
            "request": request, 
            "topic_name": canonical_name,
            "topic_uid": topic_uid,
            "topic_display_name": topic_display_name,
        }
    )

@app.get("/api/v1/topic/{topic_name}/sidebar")
async def get_topic_sidebar(topic_name: str):
    """動態撈取特定主題的 3 層 Sidebar 目錄 (包含通用與自建)"""
    topic_name = canonical_topic_slug(topic_name)
    topic_uid = _topic_uid(topic_name)
    tree = sidebar_db.get_sidebar_tree(topic_name, topic_uid)
    current_manifest = topic_content_db.get_current_manifest(topic_name)
    evidence_revisions = await asyncio.to_thread(_get_topic_evidence_revisions, current_manifest)
    statuses = topic_content_db.status_for_topic(
        topic_uid, current_manifest, evidence_revisions=evidence_revisions
    )["slots"]

    def add_status(nodes):
        for node in nodes:
            node["content_status"] = statuses.get(node["slot_id"], {}).get("status", "empty")
            add_status(node.get("children", []))

    add_status(tree["universal"])
    add_status(tree["custom"])
    return {"status": "success", "topic": topic_name, "topic_uid": topic_uid, "tree": tree}


def _topic_uid(topic_name: str) -> str:
    return sidebar_db.topic_uid_for(canonical_topic_slug(topic_name))


def _ensure_topic_manifest(topic_name: str):
    topic_name = canonical_topic_slug(topic_name)
    manifest = topic_content_db.get_current_manifest(topic_name)
    if manifest:
        return manifest
    topic_uid = _topic_uid(topic_name)
    tree = sidebar_db.get_sidebar_tree(topic_name, topic_uid)
    return topic_content_db.save_manifest(build_manifest(topic_name, topic_uid, tree))


def _mark_topic_manifest_dirty(topic_name: str):
    """Atomically rebuild the contract and queue the exact affected slots for a fresh scan."""
    topic_name = canonical_topic_slug(topic_name)
    topic_uid = _topic_uid(topic_name)
    previous = topic_content_db.get_current_manifest(topic_name)
    tree = sidebar_db.get_sidebar_tree(topic_name, topic_uid)
    candidate = build_manifest(topic_name, topic_uid, tree)
    if previous and previous.get("dom_hash") == candidate["dom_hash"]:
        return previous, None, []
    affected = affected_manifest_slot_ids(previous, candidate)
    stored, automation_job_id = topic_content_db.save_manifest_and_enqueue_scan(candidate, affected)
    return stored, automation_job_id, affected


def _public_topic_manifest(manifest):
    public_manifest = json.loads(json.dumps(manifest))
    public_manifest["screenshots"] = [
        {key: value for key, value in screenshot.items() if key != "artifact_path"}
        for screenshot in public_manifest.get("screenshots", [])
    ]
    return public_manifest


def _post_rag_json(path, payload, *, timeout, max_bytes, include_topic_token=False):
    base_url = os.getenv("EBM_RAG_BASE_URL", "http://127.0.0.1:33301").rstrip("/")
    url = f"{base_url}{path}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    generation_token = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "") if include_topic_token else ""
    if include_topic_token and generation_token:
        headers["X-LLMEBM-Topic-Token"] = generation_token
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"ebm-rag returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ebm-rag unavailable: {exc.reason}") from exc
    if len(body) > max_bytes:
        raise RuntimeError("ebm-rag response exceeded the allowed size.")
    return json.loads(body.decode("utf-8"))


def _post_topic_generation(payload):
    return _post_rag_json(
        "/api/v1/rag/topic-content/generate", payload,
        timeout=1800, max_bytes=10 * 1024 * 1024, include_topic_token=True,
    )


def _post_medpilot_query(query):
    """Reuse the production RAG query path without synthetic fallback or conversation persistence."""
    return _post_rag_json(
        "/api/v1/rag/query",
        {
            "dx_summary": query,
            "case_context": {},
            "filters": {"query_decomposition_mode": "deterministic"},
            "top_k": 8,
        },
        timeout=120,
        max_bytes=2 * 1024 * 1024,
    )


def _get_source_use_gate(paper_ids, required_use="commercial_publication"):
    """Ask ebm-rag for a fail-closed source-rights decision; never infer permissions locally."""
    if not paper_ids:
        raise ValueError("Publishable content must contain at least one source citation.")
    try:
        result = _post_rag_json(
            "/api/v1/rag/sources/use-gate",
            {"paper_ids": paper_ids, "required_use": required_use},
            timeout=10, max_bytes=1024 * 1024,
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Source-use gate is unavailable.") from exc
    if not isinstance(result, dict) or result.get("required_use") != required_use:
        raise RuntimeError("Source-use gate returned an invalid response.")
    return result


def _get_source_details(paper_ids):
    try:
        result = _post_rag_json(
            "/api/v1/rag/sources/details", {"paper_ids": paper_ids},
            timeout=10, max_bytes=1024 * 1024,
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Source details are unavailable.") from exc
    if not isinstance(result, dict) or result.get("schema") != "rootmedicals-source-details.v1":
        raise RuntimeError("Source details returned an invalid response.")
    return result


def _get_scope_review_statuses(topic_name, slot_ids):
    """Read append-only evidence mapping reviews; never infer approval in llmebm."""
    if not isinstance(slot_ids, list) or not 1 <= len(slot_ids) <= 200:
        raise ValueError("Evidence mapping review requires 1 to 200 slot IDs.")
    try:
        result = _post_rag_json(
            "/api/v1/rag/topic-content/scope-reviews/status",
            {"topic_key": topic_name, "slot_ids": slot_ids},
            timeout=10, max_bytes=1024 * 1024,
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Evidence mapping review is unavailable.") from exc
    statuses = result.get("statuses") if isinstance(result, dict) else None
    if result.get("schema") != "rootmedicals-scope-review-status.v1" or not isinstance(statuses, dict):
        raise RuntimeError("Evidence mapping review returned an invalid response.")
    if result.get("requested") != len(slot_ids) or len(statuses) != len(slot_ids):
        raise RuntimeError("Evidence mapping review did not cover the requested slots.")
    return result


def _scope_slot_key(slot_id):
    """Convert a manifest slot ID to the RAG scope key without guessing its namespace."""
    for namespace in ("universal", "custom"):
        marker = f":{namespace}:"
        if marker in str(slot_id):
            return f"{namespace}:{str(slot_id).split(marker, 1)[1]}"
    raise ValueError(f"Invalid manifest slot ID: {slot_id}")


def _get_scope_review_status(topic_name, slot_id):
    """Return one slot's current mapping-review status for the detail panel."""
    result = _get_scope_review_statuses(topic_name, [slot_id])
    statuses = result["statuses"]
    if len(statuses) != 1:
        raise RuntimeError("Evidence mapping review did not return the requested slot.")
    return next(iter(statuses.values()))


def _review_token_from_request(request):
    """Prefer an explicit header; local/dev Admin pages may use the scoped HttpOnly session cookie."""
    return request.headers.get("X-LLMEBM-Review-Token") or request.cookies.get(REVIEW_SESSION_COOKIE_NAME)


def _require_review_token(request):
    if not review_request_authorized(_review_token_from_request(request)):
        raise HTTPException(status_code=403, detail="Medical review operation is not authorized.")


def _require_hierarchy_admin_token(request):
    if not hierarchy_admin_request_authorized(request.headers.get("X-LLMEBM-Hierarchy-Token")):
        raise HTTPException(status_code=403, detail="Hierarchy admin operation is not authorized.")


async def _current_review_revisions(manifest, slot_id):
    revisions = await asyncio.to_thread(_get_topic_evidence_revisions, manifest)
    structure = manifest_structure_revisions(manifest).get(slot_id)
    if not revisions or not revisions.get(slot_id) or not structure:
        raise HTTPException(status_code=503, detail="Current evidence/structure revisions are unavailable.")
    status = topic_content_db.status_for_topic(
        manifest["topic_uid"], manifest, evidence_revisions=revisions,
    )["slots"].get(slot_id, {}).get("status")
    if status != "ready":
        raise HTTPException(status_code=409, detail="Only current ready content can enter review or publication.")
    return revisions[slot_id], structure


def _get_current_evidence_revision():
    """Read the cheap RAG readiness fingerprint; never call an LLM from page status checks."""
    base_url = os.getenv("EBM_RAG_BASE_URL", "http://127.0.0.1:33301").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/api/v1/rag/topic-content/readiness",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            return None
        payload = json.loads(body.decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    revision = (payload.get("active_embedding_index") or {}).get("evidence_revision")
    try:
        return validate_evidence_revision(revision)
    except ValueError:
        return None


def _get_topic_evidence_revisions(manifest):
    """Read per-slot fingerprints without calling an LLM; global revision is the fail-closed compatibility path."""
    if not isinstance(manifest, dict):
        return None
    slot_ids = [slot["slot_id"] for slot in manifest.get("slots", []) if slot.get("content_target", True)]
    if not slot_ids:
        return {}
    base_url = os.getenv("EBM_RAG_BASE_URL", "http://127.0.0.1:33301").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/api/v1/rag/topic-content/revisions",
        data=json.dumps({"topic_key": manifest.get("topic_name"), "slot_ids": slot_ids}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("scoped revision response exceeded 1 MiB")
        payload = json.loads(body.decode("utf-8"))
        revisions = payload.get("evidence_revisions")
        if not isinstance(revisions, dict) or set(revisions) != set(slot_ids):
            raise ValueError("scoped revision response did not cover the manifest")
        return {slot_id: validate_evidence_revision(revisions[slot_id]) for slot_id in slot_ids}
    except (OSError, ValueError, json.JSONDecodeError):
        global_revision = _get_current_evidence_revision()
        # ponytail: a total RAG/API outage must over-invalidate, never silently serve possibly stale medical content.
        return {slot_id: global_revision for slot_id in slot_ids} if global_revision else None


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
        evidence_revision = response.get("evidence_revision")
        evidence_revisions = response.get("evidence_revisions") or {}
        requested_slots = set(slot_ids)
        returned_slots = set()
        local_errors = []
        for section in response["sections"]:
            if not isinstance(section, dict) or section.get("slot_id") not in requested_slots:
                local_errors.append({"status": "invalid_response", "message": "ebm-rag returned an unrequested slot."})
                continue
            returned_slots.add(section["slot_id"])
            if not section.get("evidence_revision"):
                section["evidence_revision"] = evidence_revisions.get(section["slot_id"], evidence_revision)
            try:
                topic_content_db.save_section_result(
                    manifest["topic_uid"], manifest["dom_hash"], section, plan=plan
                )
            except (TypeError, ValueError, KeyError) as exc:
                failed = {
                    "slot_id": section.get("slot_id", "unknown"),
                    "status": "failed",
                    "evidence_revision": evidence_revisions.get(section["slot_id"], evidence_revision),
                    "error": {"stage": "response_validation", "error": str(exc)},
                }
                topic_content_db.save_section_result(manifest["topic_uid"], manifest["dom_hash"], failed, plan=plan)
                local_errors.append({"slot_id": section["slot_id"], "status": "invalid_response", "message": str(exc)})
        for missing_slot in requested_slots - returned_slots:
            missing_error = {
                "stage": "response_validation",
                "error": "ebm-rag omitted a requested slot.",
            }
            topic_content_db.save_section_result(
                manifest["topic_uid"],
                manifest["dom_hash"],
                {
                    "slot_id": missing_slot,
                    "status": "failed",
                    "evidence_revision": evidence_revisions.get(missing_slot, evidence_revision),
                    "error": missing_error,
                },
                plan=plan,
            )
            local_errors.append({"slot_id": missing_slot, "status": "missing_response", **missing_error})
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
    evidence_revisions = await asyncio.to_thread(_get_topic_evidence_revisions, manifest)
    return {
        "status": "success",
        "topic_uid": manifest["topic_uid"],
        **topic_content_db.status_for_topic(
            manifest["topic_uid"], manifest, evidence_revisions=evidence_revisions
        ),
    }


@app.get("/api/v1/topic/{topic_name}/content/{slot_id}")
async def get_topic_content(topic_name: str, slot_id: str):
    manifest = _ensure_topic_manifest(topic_name)
    allowed = {slot["slot_id"] for slot in manifest["slots"]}
    if slot_id not in allowed:
        raise HTTPException(status_code=404, detail="Unknown content slot.")
    published = topic_content_db.get_published_content(manifest["topic_uid"], slot_id)
    result = published or topic_content_db.get_current_content(manifest["topic_uid"], slot_id)
    if not result:
        return JSONResponse(status_code=404, content={"status": "empty", "slot_id": slot_id})
    return {
        "slot_id": slot_id,
        "visibility": "published" if published else "draft_preview",
        **result,
    }


@app.get("/api/v1/topic/{topic_name}/automation/status")
async def get_topic_automation_status(topic_name: str, request: Request):
    """Expose the durable scan/regeneration queue to authorized reviewers without worker secrets."""
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    queue = topic_content_db.automation_status(manifest["topic_uid"])
    headings = {slot["slot_id"]: slot["heading"] for slot in manifest.get("slots", [])}
    for job in queue["jobs"]:
        job["slot_heading"] = headings.get(job.get("slot_id"))
    return {
        "status": "success",
        "topic_uid": manifest["topic_uid"],
        **queue,
    }


@app.post("/api/v1/topic/{topic_name}/content/{slot_id}/source-details")
async def get_topic_source_details(topic_name: str, slot_id: str, payload: TopicSourceDetailsRequest):
    """Proxy only source metadata already cited by the selected llmebm-owned slot."""
    manifest = _ensure_topic_manifest(topic_name)
    if slot_id not in {slot["slot_id"] for slot in manifest["slots"]}:
        raise HTTPException(status_code=404, detail="Unknown content slot.")
    stored = (
        topic_content_db.get_published_content(manifest["topic_uid"], slot_id)
        or topic_content_db.get_current_content(manifest["topic_uid"], slot_id)
    )
    if not stored or not stored.get("content"):
        raise HTTPException(status_code=404, detail="No content is available for this slot.")
    cited_paper_ids = set(content_paper_ids(stored["content"]))
    unknown = set(payload.paper_ids).difference(cited_paper_ids)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Paper IDs are not cited by this slot: {sorted(unknown)}")
    try:
        details = await asyncio.to_thread(_get_source_details, payload.paper_ids)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "success", **details}


@app.post("/api/v1/topic/{topic_name}/content/{slot_id}/review-submit")
async def submit_topic_content_review(topic_name: str, slot_id: str, payload: TopicReviewRequest, request: Request):
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    if slot_id not in {slot["slot_id"] for slot in manifest["slots"]}:
        raise HTTPException(status_code=404, detail="Unknown content slot.")
    evidence_revision, structure_revision = await _current_review_revisions(manifest, slot_id)
    try:
        version = topic_content_db.submit_for_review(
            manifest["topic_uid"], slot_id, payload.actor, evidence_revision, structure_revision, payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "success", "version": version}


@app.post("/api/v1/topic/{topic_name}/content/{slot_id}/review-decision")
async def decide_topic_content_review(
    topic_name: str, slot_id: str, payload: TopicReviewDecisionRequest, request: Request,
):
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    await _current_review_revisions(manifest, slot_id)
    try:
        version = topic_content_db.decide_review(
            manifest["topic_uid"], slot_id, payload.actor, payload.decision, payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "success", "version": version}


@app.post("/api/v1/topic/{topic_name}/content/{slot_id}/publish")
async def publish_topic_content(topic_name: str, slot_id: str, payload: TopicReviewRequest, request: Request):
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    evidence_revision, structure_revision = await _current_review_revisions(manifest, slot_id)
    current = topic_content_db.get_current_content(manifest["topic_uid"], slot_id)
    if not current:
        raise HTTPException(status_code=404, detail="Current content is unavailable.")
    try:
        source_gate = await asyncio.to_thread(
            _get_source_use_gate, content_paper_ids(current["content"]), "commercial_publication",
        )
        version = topic_content_db.publish_current(
            manifest["topic_uid"], slot_id, payload.actor, evidence_revision, structure_revision,
            source_gate, payload.comment,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "success", "version": version, "source_gate": source_gate}


@app.post("/api/v1/topic/{topic_name}/content/{slot_id}/rollback")
async def rollback_topic_content(topic_name: str, slot_id: str, payload: TopicRollbackRequest, request: Request):
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    evidence_revision, structure_revision = await _current_review_revisions(manifest, slot_id)
    target = topic_content_db.get_workflow_version(payload.version_id)
    if not target or target["topic_uid"] != manifest["topic_uid"] or target["slot_id"] != slot_id:
        raise HTTPException(status_code=404, detail="Rollback target is unavailable.")
    try:
        source_gate = await asyncio.to_thread(
            _get_source_use_gate, target["paper_ids"], "commercial_publication",
        )
        version = topic_content_db.rollback_publication(
            manifest["topic_uid"], slot_id, payload.version_id, payload.actor,
            evidence_revision, structure_revision, source_gate, payload.comment,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "success", "version": version, "source_gate": source_gate}


@app.get("/api/v1/topic/{topic_name}/content/{slot_id}/review-history")
async def get_topic_content_review_history(topic_name: str, slot_id: str, request: Request):
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    return {
        "status": "success",
        "history": topic_content_db.get_review_history(manifest["topic_uid"], slot_id),
    }


@app.get("/api/v1/topic/{topic_name}/review-queue")
async def get_topic_review_queue(topic_name: str, request: Request):
    """List current-ready versions without exposing their clinical body in the queue response."""
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    slot_index = {slot["slot_id"]: slot for slot in manifest["slots"]}
    evidence_revisions = await asyncio.to_thread(_get_topic_evidence_revisions, manifest)
    live_status = topic_content_db.status_for_topic(
        manifest["topic_uid"], manifest, evidence_revisions=evidence_revisions,
    )["slots"]
    items = [
        item for item in topic_content_db.list_review_queue(manifest["topic_uid"])
        if live_status.get(item["slot_id"], {}).get("status") == "ready"
    ]
    for item in items:
        slot = slot_index.get(item["slot_id"], {})
        item["heading"] = slot.get("heading", item["slot_id"])
        item["heading_path"] = slot.get("heading_path", [])
        item["order"] = slot.get("order")
    counts = {status: 0 for status in ("generated", "review_pending", "approved", "published", "rejected", "retired")}
    for item in items:
        counts[item["workflow_status"]] += 1
    return {"status": "success", "topic_uid": manifest["topic_uid"], "counts": counts, "items": items}


@app.get("/api/v1/topic/{topic_name}/scope-reviews")
async def get_topic_scope_reviews(topic_name: str, request: Request):
    """List reviewed mappings plus bounded current statuses needed by the generation gate UI."""
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    slots = [slot for slot in manifest["slots"] if slot.get("content_target", True)]
    slot_ids = [slot["slot_id"] for slot in slots]
    try:
        result = await asyncio.to_thread(_get_scope_review_statuses, manifest["topic_name"], slot_ids)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    statuses = result["statuses"]
    items = []
    all_items = []
    for slot in slots:
        slot_id = slot["slot_id"]
        slot_key = _scope_slot_key(slot_id)
        review = statuses.get(slot_key, {})
        enriched = {
            **review,
            "slot_id": slot_id,
            "heading": slot["heading"],
            "heading_path": slot.get("heading_path", []),
            "order": slot.get("order"),
        }
        all_items.append(enriched)
        if not review.get("reviewed"):
            continue
        items.append(enriched)
    return {
        "status": "success",
        "schema": result["schema"],
        "topic_uid": manifest["topic_uid"],
        "requested": result["requested"],
        "reviewed": result["reviewed"],
        "current_approved": result["current_approved"],
        "items": items,
        "all_items": all_items,
    }


@app.post("/api/v1/topic/{topic_name}/scope-reviews/{slot_id}/approve-current")
async def approve_topic_scope_review(
    topic_name: str, slot_id: str, payload: TopicReviewRequest, request: Request,
):
    """Authorize one exact current source-to-slot mapping through the append-only RAG review ledger."""
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    allowed = {slot["slot_id"] for slot in manifest["slots"] if slot.get("content_target", True)}
    if slot_id not in allowed:
        raise HTTPException(status_code=404, detail="Unknown content slot.")
    reason = payload.comment.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A mapping review reason is required.")
    try:
        result = await asyncio.to_thread(
            _post_rag_json,
            "/api/v1/rag/topic-content/scope-reviews/approve-current",
            {
                "topic_key": manifest["topic_name"],
                "slot_id": _scope_slot_key(slot_id),
                "reviewed_by": payload.actor,
                "reason": reason,
            },
            timeout=10,
            max_bytes=1024 * 1024,
            include_topic_token=True,
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    status = result.get("status") if isinstance(result, dict) else None
    if not isinstance(result, dict) or result.get("schema") != "rootmedicals-scope-review-approval.v1" or not isinstance(status, dict):
        raise HTTPException(status_code=502, detail="Evidence mapping approval returned an invalid response.")
    if status.get("slot_key") != _scope_slot_key(slot_id) or not status.get("current_approved"):
        raise HTTPException(status_code=409, detail="Evidence mapping approval did not bind to the current slot scope.")
    resumed = topic_content_db.resume_waiting_regeneration(manifest["topic_uid"], slot_id)
    return {
        "status": "success",
        "topic_uid": manifest["topic_uid"],
        "automation_jobs_resumed": resumed,
        **result,
    }


@app.get("/api/v1/topic/{topic_name}/content/{slot_id}/review-detail")
async def get_topic_review_detail(topic_name: str, slot_id: str, request: Request):
    """Return the draft/published comparison, source gate, and audit trail for one cited slot."""
    _require_review_token(request)
    manifest = _ensure_topic_manifest(topic_name)
    slot_index = {slot["slot_id"]: slot for slot in manifest["slots"]}
    slot = slot_index.get(slot_id)
    if not slot:
        raise HTTPException(status_code=404, detail="Unknown content slot.")
    current = topic_content_db.get_current_content(manifest["topic_uid"], slot_id)
    if not current or not current.get("content"):
        raise HTTPException(status_code=404, detail="No current content is available for review.")
    paper_ids = content_paper_ids(current["content"])
    try:
        source_gate = await asyncio.to_thread(_get_source_use_gate, paper_ids, "commercial_publication")
    except RuntimeError as exc:
        source_gate = {
            "allowed": False,
            "required_use": "commercial_publication",
            "paper_ids": paper_ids,
            "blocked": [{"reason": "source_gate_unavailable", "detail": str(exc)}],
        }
    try:
        scope_review = await asyncio.to_thread(_get_scope_review_status, manifest["topic_name"], slot_id)
    except RuntimeError as exc:
        scope_review = {
            "reviewed": False,
            "current_approved": False,
            "reason": "scope_review_unavailable",
            "detail": str(exc),
        }
    return {
        "status": "success",
        "slot": {
            "slot_id": slot_id,
            "heading": slot["heading"],
            "heading_path": slot.get("heading_path", []),
        },
        "current": current,
        "published": topic_content_db.get_published_content(manifest["topic_uid"], slot_id),
        "source_gate": source_gate,
        "scope_review": scope_review,
        "history": topic_content_db.get_review_history(manifest["topic_uid"], slot_id),
    }


@app.post("/api/v1/admin/topics/{topic_name}/hierarchy/preview")
async def preview_topic_hierarchy(topic_name: str, payload: HierarchyPreviewRequest, request: Request):
    _require_hierarchy_admin_token(request)
    try:
        return {
            "status": "success",
            "preview": sidebar_db.preview_hierarchy_change(
                topic_name, payload.source, payload.node_key, payload.change,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/admin/topics/{topic_name}/hierarchy/apply")
async def apply_topic_hierarchy(topic_name: str, payload: HierarchyApplyRequest, request: Request):
    _require_hierarchy_admin_token(request)
    try:
        receipt = sidebar_db.apply_hierarchy_change(
            topic_name, payload.source, payload.node_key, payload.change, payload.actor,
            payload.expected_before_hash, payload.comment,
        )
        manifest, automation_job_id, affected_slot_ids = _mark_topic_manifest_dirty(topic_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "status": "success",
        "receipt": receipt,
        "manifest_hash": manifest["dom_hash"],
        "automation": {
            "scan_job_id": automation_job_id,
            "affected_slot_ids": affected_slot_ids,
        },
    }


@app.post("/api/v1/admin/topics/{topic_name}/hierarchy/rollback")
async def rollback_topic_hierarchy(topic_name: str, payload: HierarchyRollbackRequest, request: Request):
    _require_hierarchy_admin_token(request)
    try:
        receipt = sidebar_db.rollback_hierarchy_change(
            payload.audit_id, payload.actor, payload.comment,
            sidebar_db.topic_uid_for(canonical_topic_slug(topic_name)),
        )
        manifest, automation_job_id, affected_slot_ids = _mark_topic_manifest_dirty(topic_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "status": "success",
        "receipt": receipt,
        "manifest_hash": manifest["dom_hash"],
        "automation": {
            "scan_job_id": automation_job_id,
            "affected_slot_ids": affected_slot_ids,
        },
    }


@app.get("/api/v1/search")
async def search_llmebm(q: str = "", limit: int = 20):
    """Search llmebm-owned topic headings; external reference content is never indexed here."""
    if len(q) > 120:
        raise HTTPException(status_code=400, detail="q must contain at most 120 characters.")
    results = sidebar_db.search_topics_and_sections(q, limit)
    return {"status": "success", "query": q.strip(), "results": results}


@app.post("/api/v1/medpilot/query")
async def query_medpilot(payload: MedpilotQueryRequest):
    """Run one read-only EBM query; greetings are local and untraceable clinical answers fail closed."""
    greeting = smalltalk_response(payload.query)
    if greeting is not None:
        return greeting
    try:
        rag_result = await asyncio.to_thread(_post_medpilot_query, payload.query)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        # Provider/network detail can include secrets or topology; expose only a stable retryable boundary.
        raise HTTPException(status_code=503, detail="Medpilot evidence retrieval is temporarily unavailable.") from exc
    return build_medpilot_response(rag_result)


@app.get("/api/v1/topic/{topic_name}/user-state")
async def get_topic_user_state(topic_name: str):
    topic_uid = _topic_uid(topic_name)
    return {"status": "success", "topic_uid": topic_uid, **topic_content_db.get_user_state(topic_uid)}


@app.put("/api/v1/topic/{topic_name}/user-state")
async def update_topic_user_state(topic_name: str, payload: TopicUserStateRequest):
    topic_uid = _topic_uid(topic_name)
    try:
        state = topic_content_db.set_user_state(
            topic_uid, followed=payload.followed, mark_seen=payload.mark_seen,
            seen_status_hash=payload.seen_status_hash,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "topic_uid": topic_uid, **state}


@app.post("/api/v1/topic/{topic_name}/content/generate")
async def generate_topic_content(topic_name: str, payload: TopicGenerateRequest, http_request: Request):
    topic_token = http_request.headers.get("X-LLMEBM-Topic-Token")
    operator_authorized = generation_request_authorized(
        http_request.client.host if http_request.client else "", topic_token,
    )
    reviewer_authorized = review_request_authorized(_review_token_from_request(http_request))
    if not operator_authorized and not reviewer_authorized:
        raise HTTPException(status_code=403, detail="Topic generation is not authorized.")
    # A browser never receives the generation token. A review token gets only this narrow single-slot mode.
    reviewer_ui_mode = reviewer_authorized and not topic_token
    if reviewer_ui_mode:
        try:
            validate_review_generation_payload(
                payload.only_slot_ids,
                force=payload.force,
                require_current_scope_review=payload.require_current_scope_review,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        evidence_revisions = await asyncio.to_thread(_get_topic_evidence_revisions, manifest)
        statuses = topic_content_db.status_for_topic(
            manifest["topic_uid"], manifest, evidence_revisions=evidence_revisions
        )["slots"]
        slot_ids = [
            slot["slot_id"] for slot in manifest["slots"]
            if payload.force or statuses[slot["slot_id"]]["status"] in {"empty", "stale", "failed"}
        ]
    if payload.only_slot_ids and not payload.force:
        evidence_revisions = await asyncio.to_thread(_get_topic_evidence_revisions, manifest)
        statuses = topic_content_db.status_for_topic(
            manifest["topic_uid"], manifest, evidence_revisions=evidence_revisions,
        )["slots"]
        slot_ids = [
            slot_id for slot_id in slot_ids
            if (statuses.get(slot_id) or {}).get("status") in {"empty", "stale", "failed"}
        ]
    if not slot_ids:
        return {"status": "unchanged", "message": "No empty, stale, or failed slots require generation."}
    if reviewer_ui_mode:
        evidence_revisions = await asyncio.to_thread(_get_topic_evidence_revisions, manifest)
        statuses = topic_content_db.status_for_topic(
            manifest["topic_uid"], manifest, evidence_revisions=evidence_revisions,
        )["slots"]
        try:
            validate_review_generation_status((statuses.get(slot_ids[0]) or {}).get("status"))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    scope_review_count = 0
    if payload.require_current_scope_review:
        try:
            review_result = await asyncio.to_thread(
                _get_scope_review_statuses, manifest["topic_name"], slot_ids,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        review_statuses = review_result["statuses"]
        pending = [
            slot_id for slot_id in slot_ids
            if not review_statuses.get(_scope_slot_key(slot_id), {}).get("current_approved")
        ]
        if pending:
            raise HTTPException(
                status_code=409,
                detail=f"Current evidence mapping review is required for: {', '.join(pending)}",
            )
        scope_review_count = len(slot_ids)
    try:
        screenshots = load_screenshot_payloads(manifest)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job_id = topic_content_db.create_job(manifest["topic_uid"], manifest["dom_hash"], slot_ids)
    asyncio.create_task(_run_topic_generation(job_id, manifest, screenshots, slot_ids, payload.filters, payload.top_k))
    return JSONResponse(status_code=202, content={
        "status": "accepted",
        "job_id": job_id,
        "slot_ids": slot_ids,
        "scope_review_gate": {
            "required": payload.require_current_scope_review,
            "current_approved": scope_review_count,
        },
    })

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
async def update_system_settings(settings: SystemSettingsModel, request: Request):
    """
    更新系統全域品牌設定，寫入 SQLite 資料庫持久化儲存。
    """
    _require_hierarchy_admin_token(request)
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

@app.get("/api/v1/analyze-soap")
async def analyze_soap_endpoint():
    """Explicit tombstone for stale clients; the static clinical demo must never revive."""
    return JSONResponse(
        status_code=410,
        content={
            "status": "removed",
            "message": "Use the dynamic Topic Page for evidence-backed content.",
        },
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
    request: Request,
    file: UploadFile = File(...), 
    parent_id: str = Form("null")
):
    """
    接收 Admin 介面上傳的 CSV 檔案與其從屬的 parent_id，
    透過模型將資料批次寫入 specialty.db 的 taxonomy_nodes 資料表中。
    """
    try:
        _require_hierarchy_admin_token(request)
        content = await file.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise HTTPException(status_code=413, detail="CSV upload exceeds 1 MiB.")
        csv_text = content.decode('utf-8')
        
        # 處理 null 字串的防呆邏輯
        p_id = None if parent_id == "null" or not parent_id.isdigit() else int(parent_id)
        
        inserted = admin_db.batch_insert_from_csv(p_id, csv_text)
        return {"status": "success", "message": f"Successfully imported {inserted} nodes from CSV."}
    except HTTPException:
        raise
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be valid UTF-8.") from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =========================================================================
# 單筆手動 CRUD API 路由
# =========================================================================
@app.post("/api/v1/admin/taxonomy/node")
async def create_taxonomy_node(node: NodeCreate, request: Request):
    """
    單筆或多筆新增分類節點 (支援逗號分隔)
    [v1.4 擴充] 系統自動解析逗號，將字串拆分為多筆獨立資料寫入資料庫
    """
    _require_hierarchy_admin_token(request)
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
async def update_taxonomy_node(node_id: int, node: NodeUpdate, request: Request):
    """單筆修改分類節點名稱"""
    _require_hierarchy_admin_token(request)
    try:
        admin_db.update_node(node_id, node.name)
        return {"status": "success", "message": f"Node updated to '{node.name}'."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/v1/admin/taxonomy/node/{node_id}")
async def delete_taxonomy_node(node_id: int, request: Request):
    """單筆刪除分類節點"""
    _require_hierarchy_admin_token(request)
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
async def create_sidebar_node(topic_name: str, node: SidebarNodeCreate, request: Request):
    """新增 Specialized Features 節點"""
    _require_hierarchy_admin_token(request)
    try:
        new_id = sidebar_db.add_custom_node(topic_name, node.parent_id, node.name)
        manifest, automation_job_id, affected_slot_ids = _mark_topic_manifest_dirty(topic_name)
        return {
            "status": "success", "message": "Node added.", "node_id": new_id,
            "scan_required": bool(automation_job_id), "dom_hash": manifest["dom_hash"],
            "automation": {"scan_job_id": automation_job_id, "affected_slot_ids": affected_slot_ids},
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/v1/topic/sidebar/node/{node_id}")
async def delete_sidebar_node(node_id: int, request: Request):
    """刪除 Specialized Features 節點"""
    _require_hierarchy_admin_token(request)
    try:
        topic_name = sidebar_db.delete_custom_node(node_id)
        manifest, automation_job_id, affected_slot_ids = _mark_topic_manifest_dirty(topic_name)
        return {
            "status": "success", "message": "Node deleted.",
            "scan_required": bool(automation_job_id), "dom_hash": manifest["dom_hash"],
            "automation": {"scan_job_id": automation_job_id, "affected_slot_ids": affected_slot_ids},
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main_ebm:app", host="0.0.0.0", port=8000, reload=True)
