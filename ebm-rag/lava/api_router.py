# 檔案路徑: rootmedicals-a/ebm-rag/lava/api_router.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/api_router.py
# Timestamp: 2026-06-16
# Version: v0.8
# Description: LAVA FastAPI APIRouter。掛載至 main_rag.py。
#              提供 connections / models / verify / tasks / bindings / invoke 等路由。
#              所有回應為 JSON；API key 絕不明文回傳；task binding 必須連到 capability-verified active connection。
# Change Notes:
#              - v0.7: Registered llmxx-server optional clinical_soap_parse
#                and llmaaj_adjudicate executors.
#              - v0.8: Registered rag_query_strategy executor so the dedicated
#                physician-question-to-RAG-planning LAVA task is invokable.
# ----------------------------------------------------------------------------------------------------

import importlib
from datetime import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from lava.schemas import ConnectionCreate, ConnectionPatch, FetchModelsRequest, VerifyRequest, BindingUpdate, TaskInvokeRequest
from lava.task_registry import RAG_TASKS, TASK_IDS
from lava.llm_model import LLMModel

router = APIRouter(prefix="/api/lava", tags=["lava"])

_TASK_EXECUTORS = {
    "semantic_reconstruct": ("lava.matching_tasks.semantic_reconstruct", "execute_semantic_reconstruction"),
    "embedding_dense": ("rag_core.core2_embeddings.embedding_client", "embed_texts"),
    "ebm_generate": ("lava.matching_tasks.ebm_generate", "execute_ebm_generate"),
    "query_decompose": ("lava.matching_tasks.query_decompose", "execute_query_decompose"),
    "rag_query_strategy": ("lava.matching_tasks.rag_query_strategy", "execute_rag_query_strategy"),
    "topic_content_plan": ("lava.matching_tasks.topic_content_plan", "execute_topic_content_plan"),
    "topic_content_compose": ("lava.matching_tasks.topic_content_compose", "execute_topic_content_compose"),
    "synthetic_ebm_candidate": ("lava.matching_tasks.synthetic_ebm_candidate", "execute_synthetic_ebm_candidate"),
    "claim_verify": ("lava.matching_tasks.claim_verify", "execute_claim_verify"),
    "clinical_soap_parse": ("lava.matching_tasks.clinical_soap_parse", "execute_clinical_soap_parse"),
    "llmaaj_adjudicate": ("lava.matching_tasks.llmaaj_adjudicate", "execute_llmaaj_adjudicate")
}


def _mask_key(row: dict) -> dict:
    if not row:
        return row
    if "api_key" in row:
        key = row.get("api_key") or ""
        row["api_key"] = key[:4] + "****" if len(key) > 4 else ("****" if key else "")
    if "api_key_enc" in row:
        # Never expose encrypted/raw key field in external API response.
        row.pop("api_key_enc", None)
    return row


def _module_implemented(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def _connection_readiness(row: dict | None, required_capability: str | None = None) -> tuple[bool, str | None]:
    if not row:
        return False, "connection_not_found"
    conn = dict(row)
    if not conn.get("has_key"):
        return False, "missing_api_key"
    if not conn.get("model_id"):
        return False, "missing_model_id"
    if not conn.get("is_active"):
        return False, "connection_inactive"
    if conn.get("verify_status") != "ok":
        return False, "connection_not_verified"
    if required_capability:
        from lava.adapter import get_adapter
        adapter = get_adapter(conn.get("provider", ""))
        if not adapter:
            return False, "unknown_provider"
        if required_capability == "embedding" and not adapter.supports_embedding:
            return False, "provider_does_not_support_embedding"
        if required_capability == "chat" and not adapter.supports_chat:
            return False, "provider_does_not_support_chat"
        if required_capability == "vision" and not adapter.supports_vision:
            return False, "provider_does_not_support_vision"
        if conn.get("verified_capability") != required_capability:
            return False, f"connection_not_verified_for_{required_capability}"
    return True, None


def _record_verification_failure(connection_id: int, row: dict, error_message: str) -> bool:
    preserved = bool(row.get("is_active") and row.get("verify_status") == "ok" and row.get("verified_capability"))
    if preserved:
        LLMModel.update_connection(
            connection_id,
            last_error=error_message
        )
        return True
    LLMModel.update_connection(
        connection_id,
        is_active=0,
        verify_status="failed",
        verified_capability="",
        last_error=error_message
    )
    return False


def _task_readiness() -> dict:
    bindings = LLMModel.list_bindings() or []
    binding_map = {b["task_id"]: b.get("connection_id") for b in bindings}
    tasks = {}
    all_ready = True
    for task_def in RAG_TASKS:
        task_id = task_def["task_id"]
        required = bool(task_def.get("required", False))
        implemented = _module_implemented(task_def["module"])
        connection_id = binding_map.get(task_id)
        conn = LLMModel.get_connection(connection_id, include_secret=False) if connection_id else None
        connection_ready, reason = _connection_readiness(conn, task_def["capability"])
        ready = bool(implemented and connection_ready)
        if required:
            all_ready = all_ready and ready
        tasks[task_id] = {
            "ready": ready,
            "implemented": implemented,
            "required": required,
            "capability": task_def["capability"],
            "connection_id": connection_id,
            "reason": None if ready else ("task_module_not_implemented" if not implemented else reason)
        }
    return {"ready": all_ready, "tasks": tasks}


@router.get("/health")
async def lava_health():
    readiness = _task_readiness()
    return {"ok": readiness["ready"], "service": "LAVA", "readiness": readiness}


@router.get("/connections")
async def list_connections():
    rows = LLMModel.list_connections()
    return [_mask_key(dict(r)) for r in rows]


@router.post("/connections")
async def create_connection(body: ConnectionCreate):
    row = LLMModel.create_connection(
        provider=body.provider, model_id=body.model_id,
        name=body.name, api_key=body.api_key,
        rpm_limit=body.rpm_limit, tpm_limit=body.tpm_limit
    )
    return _mask_key(dict(row))


@router.patch("/connections/{connection_id}")
async def patch_connection(connection_id: int, body: ConnectionPatch):
    existing = LLMModel.get_connection(connection_id, include_secret=False)
    if not existing:
        raise HTTPException(status_code=404, detail="Connection not found")
    updates = body.model_dump(exclude_none=True)
    if updates.get("is_active") in {1, True}:
        existing_dict = dict(existing)
        if not existing_dict.get("has_key"):
            raise HTTPException(status_code=409, detail="Connection cannot be activated before API key is stored")
        if not existing_dict.get("model_id"):
            raise HTTPException(status_code=409, detail="Connection cannot be activated before model_id is selected")
        if existing_dict.get("verify_status") != "ok":
            raise HTTPException(status_code=409, detail="Connection cannot be activated before verify passes")
        if not existing_dict.get("verified_capability"):
            raise HTTPException(status_code=409, detail="Connection cannot be activated before capability verify passes")

    if any(field in updates for field in ["provider", "model_id", "api_key"]):
        updates["is_active"] = 0
        updates["verify_status"] = "unverified"
        updates["verified_capability"] = ""
        updates["verified_at"] = None
        updates["last_error"] = "Connection settings changed; re-verify required"

    LLMModel.update_connection(connection_id, **updates)
    row = LLMModel.get_connection(connection_id, include_secret=False)
    return _mask_key(dict(row))


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: int):
    existing = LLMModel.get_connection(connection_id, include_secret=False)
    if not existing:
        raise HTTPException(status_code=404, detail="Connection not found")
    LLMModel.delete_connection(connection_id)
    return {"ok": True}


@router.post("/models/fetch")
async def fetch_models(body: FetchModelsRequest):
    from lava.adapter import get_adapter
    adapter = get_adapter(body.provider)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider}")
    try:
        models = await adapter.fetch_models(body.api_key)
        return {"ok": True, "models": models}
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})


@router.post("/connections/verify")
async def verify_connection(body: VerifyRequest):
    row = LLMModel.get_connection(body.connection_id, include_secret=True)
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found")
    row = dict(row)
    from lava.adapter import get_adapter
    adapter = get_adapter(row["provider"])
    if not adapter:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unknown provider: {row['provider']}"})
    if not row.get("api_key"):
        return JSONResponse(status_code=409, content={"ok": False, "error": "API key is required before verification"})
    if not row.get("model_id"):
        return JSONResponse(status_code=409, content={"ok": False, "error": "model_id is required before verification"})
    try:
        if body.capability == "embedding":
            result = await adapter.verify_embedding(row["api_key"], row["model_id"])
        elif body.capability == "vision":
            result = await adapter.verify_vision(row["api_key"], row["model_id"])
        else:
            result = await adapter.verify_chat(row["api_key"], row["model_id"])
            if result.get("ok"):
                result["capability"] = "chat"
        if result.get("ok"):
            LLMModel.update_connection(
                body.connection_id,
                is_active=1,
                verify_status="ok",
                verified_capability=body.capability,
                verified_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                last_error=""
            )
        else:
            error_message = result.get("error", "verification_failed")
            preserved = _record_verification_failure(body.connection_id, row, error_message)
            result["preserved_existing_verification"] = preserved
        return result
    except Exception as e:
        error_message = str(e)
        preserved = _record_verification_failure(body.connection_id, row, error_message)
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": error_message, "preserved_existing_verification": preserved}
        )


@router.get("/tasks")
async def list_tasks():
    out = []
    for t in RAG_TASKS:
        implemented = False
        implemented = _module_implemented(t["module"])
        out.append({**t, "implemented": implemented})
    return out


@router.get("/readiness")
async def lava_readiness():
    return _task_readiness()


@router.get("/bindings")
async def list_bindings():
    rows = LLMModel.list_bindings()
    return [dict(r) for r in rows]


@router.put("/bindings/{task_id}")
async def update_binding(task_id: str, body: BindingUpdate):
    if task_id not in TASK_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown task_id: {task_id}")
    conn = LLMModel.get_connection(body.connection_id, include_secret=False)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    # Validate capability
    task_def = next(t for t in RAG_TASKS if t["task_id"] == task_id)
    if not _module_implemented(task_def["module"]):
        raise HTTPException(status_code=409, detail=f"Task module is not implemented: {task_def['module']}")
    conn_dict = dict(conn)
    from lava.adapter import get_adapter
    adapter = get_adapter(conn_dict["provider"])
    ready, reason = _connection_readiness(conn_dict, task_def["capability"])
    if not ready:
        raise HTTPException(status_code=409, detail=f"Connection is not ready for {task_id}: {reason}")
    if task_def["capability"] == "embedding" and adapter and not adapter.supports_embedding:
        raise HTTPException(status_code=400, detail=f"Provider {conn_dict['provider']} does not support embedding")
    if task_def["capability"] == "vision" and adapter and not adapter.supports_vision:
        raise HTTPException(status_code=400, detail=f"Provider {conn_dict['provider']} does not support vision")
    LLMModel.update_binding(task_id, body.connection_id)
    return {"ok": True, "task_id": task_id, "connection_id": body.connection_id}


@router.post("/tasks/{task_id}/invoke")
async def invoke_task(task_id: str, body: TaskInvokeRequest):
    if task_id not in TASK_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown task_id: {task_id}")
    readiness = _task_readiness()
    task_status = readiness.get("tasks", {}).get(task_id, {})
    if not task_status.get("ready"):
        return JSONResponse(status_code=409, content={"ok": False, "error": "task_not_ready", "readiness": task_status})
    try:
        module_name, fn_name = _TASK_EXECUTORS.get(task_id, ("", ""))
        if not module_name:
            return JSONResponse(status_code=501, content={"ok": False, "error": f"No executor registered for {task_id}"})
        mod = importlib.import_module(module_name)
        fn = getattr(mod, fn_name, None)
        if not fn:
            return JSONResponse(status_code=501, content={"ok": False, "error": f"{fn_name} not found"})
        if task_id == "embedding_dense":
            texts = body.payload.get("texts") if isinstance(body.payload, dict) else None
            if not isinstance(texts, list):
                return JSONResponse(status_code=400, content={"ok": False, "error": "embedding_dense payload.texts must be a list"})
            result = await fn(texts)
        elif task_id == "semantic_reconstruct":
            payload = body.payload or {}
            source_text = payload.get("text") if isinstance(payload, dict) else None
            source_blocks = payload.get("blocks") if isinstance(payload, dict) else None
            if source_text is None and source_blocks is None:
                return JSONResponse(status_code=400, content={"ok": False, "error": "semantic_reconstruct payload.text or payload.blocks is required"})
            result = await fn(source_text if source_text is not None else source_blocks)
        else:
            result = await fn(body.payload)
        return {"ok": True, "result": result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
