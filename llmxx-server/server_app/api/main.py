# 檔案路徑: rootmedicals-a/llmxx-server/server_app/api/main.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.2-intake route 交接註解
# 模組定位:
#   FastAPI 對外入口。醫師端 thin client、replay script、demo viewer 都從這裡進出 llmxx-server。
# 主要責任:
#   1. 接收 /api/intake，辨識 screenshot payload、encrypted payload 或舊 formal payload。
#   2. 建立 session/event log，串接 server OCR、clinical mapper、LAVA、RAG、final gate。
#   3. 提供 /api/health、/api/demo/latest、session detail/events 給控制台與展示頁使用。
# 呼叫來源:
#   RootMedicals-Control 啟動 uvicorn；thin capture client POST 到 /api/intake。
# 輸入契約:
#   screenshot schema 由 llmxx-client-thin-capture 送出；formal schema 僅保留給 replay 與舊測試。
# 輸出契約:
#   所有回應都必須是最小化 JSON，不回傳 raw screenshot、raw OCR wrapper 或 API key。
# 安全邊界:
#   本層負責拒絕 diagnostics wrapper、非法 schema、解密失敗與未預期 payload shape。
#   LAVA/RAG 失敗不應讓 server 崩潰；要轉為可展示、可追查的黃燈或橘燈狀態。
# 維護提醒:
#   調整 intake 流程時，要同步檢查 state_db event log、doctor alert polling、/demo/latest viewer。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ..core.adjudicator import adjudicate, deterministic_adjudication
from ..core.clinical_mapper import map_payload_to_clinical
from ..core.demo_fixtures import build_demo_ebm_fixture
from ..infra.errors import get_error
from ..integrations.lava_client import invoke_lava_task
from ..integrations.rag_client import build_rag_check_request, call_rag_check
from ..core.response_builder import build_degraded_response, build_success_response
from ..contracts.schemas import ClinicalParse, EncryptedEnvelope, FormalClientPayload, ScreenshotClientPayload
from ..infra.security import SafeIntakeError, decrypt_envelope, payload_hash, patient_uid_ref, reject_forbidden_payload_shape, safe_json_loads
from ..ocr.server_ocr import missing_ocr_dependencies, screenshot_payload_to_formal
from ..infra.settings import STATIC_DIR, ensure_runtime_dirs, settings
from ..infra.state_db import db, utc_now


app = FastAPI(title="RootMedicals llmxx-server", version=settings.app_version)
ensure_runtime_dirs()
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _error_response(error_code: str, session_id: str | None = None, message: str | None = None) -> JSONResponse:
    spec = get_error(error_code)
    return JSONResponse(
        status_code=spec.http_status,
        content={
            "ok": False,
            "status": spec.status,
            "error_code": error_code,
            "retryable": spec.retryable,
            "message": message or spec.message,
            "session_id": session_id,
        },
    )


def _parse_payload(raw_payload: dict[str, Any]) -> FormalClientPayload:
    # 這裡是支援「新 thin client」與「舊 formal payload replay」的相容入口。
    # 不要把 screenshot schema 往後傳；必須先轉成 FormalClientPayload，後面才有穩定契約。
    schema_version = str(raw_payload.get("schema_version") or "")
    if schema_version == "llmxx-client-screenshot.v0.1":
        try:
            screenshot_payload = ScreenshotClientPayload.model_validate(raw_payload)
            return screenshot_payload_to_formal(screenshot_payload)
        except ValidationError as exc:
            raise SafeIntakeError("invalid_payload") from exc
        except Exception as exc:
            raise SafeIntakeError("invalid_payload", str(exc)[:180]) from exc
    if schema_version == "llmxx-client-local-ocr.encrypted.v0.1":
        try:
            EncryptedEnvelope.model_validate(raw_payload)
        except ValidationError as exc:
            raise SafeIntakeError("aes_envelope_malformed") from exc
        decrypted = decrypt_envelope(raw_payload)
        reject_forbidden_payload_shape(decrypted)
        raw_payload = decrypted
        schema_version = str(raw_payload.get("schema_version") or "")
    else:
        reject_forbidden_payload_shape(raw_payload)
    if schema_version != "llmxx-client-local-ocr.v0.1":
        raise SafeIntakeError("unsupported_schema_version")
    try:
        return FormalClientPayload.model_validate(raw_payload)
    except ValidationError as exc:
        error_text = str(exc)
        if "extra_forbidden" in error_text:
            raise SafeIntakeError("diagnostics_wrapper_rejected") from exc
        raise SafeIntakeError("invalid_payload") from exc


async def _maybe_enrich_clinical_parse(payload: FormalClientPayload, clinical: ClinicalParse) -> ClinicalParse:
    # LAVA 的 clinical_soap_parse 是加分項，不是唯一解析來源。
    # 若 LAVA 未綁定、逾時或回傳格式不對，保留 deterministic parse，避免整個閉環中斷。
    lava_payload = {
        "soap": payload.soap.model_dump(),
        "vital_signs": payload.vital_signs.model_dump(),
        "clinical_parse": clinical.model_dump(),
    }
    result = await invoke_lava_task("clinical_soap_parse", lava_payload)
    if not result.ok:
        return clinical
    candidate = result.result
    if str(candidate.get("status") or "").lower() != "ok":
        return clinical
    merged = clinical.model_dump()
    for key in ["dx", "tx", "hx"]:
        value = str(candidate.get(key) or "").strip()
        if value:
            merged[key] = value[:700]
    for key in ["parse_confidence", "missing_fields", "uncertainty_flags", "labs", "age", "sex"]:
        if key in candidate:
            merged[key] = candidate[key]
    merged["llm_status"] = "ok"
    return ClinicalParse.model_validate(merged)


async def _complete_rag_background(
    *,
    session_id: str,
    client_session_id: str,
    correlation_id: str,
    clinical: ClinicalParse,
) -> None:
    # 前景 RAG timeout 時先回醫師端黃燈，背景仍嘗試補完 session。
    # 這樣醫師端不會卡住，但 demo viewer 仍有機會稍後看到完整 event/result。
    db.add_event(session_id, "rag", "info", "background_started", "Background RAG completion started after foreground timeout.", None, None)
    rag_result, _ = await call_rag_check(clinical, timeout_seconds=settings.rag_background_timeout_seconds)
    if not rag_result.ok:
        db.add_event(
            session_id,
            "rag",
            "warning",
            "background_degraded",
            "Background RAG completion did not produce an evaluable result.",
            rag_result.error_code,
            {"retryable": get_error(rag_result.error_code or "rag_unavailable").retryable},
        )
        return

    db.add_event(session_id, "rag", "info", "background_completed", "Background RAG /check returned ebm_hits.", None, {"query_id": rag_result.payload.get("query_id")})
    adjudication = await adjudicate(clinical, rag_result.payload)
    response = build_success_response(
        session_id=session_id,
        client_session_id=client_session_id,
        correlation_id=correlation_id,
        clinical=clinical,
        ebm_hits=rag_result.payload,
        adjudication=adjudication,
    )
    db.update_session(
        session_id,
        status=response["status"],
        completed_at=utc_now(),
        error_code=response.get("error_code"),
        degraded_reason=None,
        rag_response_json=json.dumps(rag_result.payload, ensure_ascii=False, sort_keys=True),
        rag_query_id=response["ebm"].get("rag_query_id"),
        light_color=response["final_gate"].get("light_color"),
        final_score=float(adjudication.get("evidence_support_score") or 0),
        response_json=json.dumps(response, ensure_ascii=False, sort_keys=True),
    )
    db.add_event(session_id, "final_gate", "info", "background_completed", "Background final deterministic gate evaluated.", None, {"evidence_backed": response["final_gate"].get("evidence_backed")})


@app.exception_handler(Exception)
async def safe_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"ok": False, "status": "failed", "error_code": "internal_error", "retryable": True, "message": "Internal server error."},
    )


@app.get("/api/health")
async def health():
    missing_ocr = missing_ocr_dependencies()
    return {
        "ok": not missing_ocr,
        "status": "ok" if not missing_ocr else "not_ready",
        "service": settings.service_name,
        "version": settings.app_version,
        "checks": {
            "db": "ok",
            "ocr": "ready" if not missing_ocr else f"missing: {', '.join(missing_ocr)}",
            "rag": "configured" if settings.rag_check_url else "missing",
            "lava": "configured" if settings.lava_task_base_url else "missing",
            "demo_fixture": "enabled" if settings.demo_fixture_mode else "disabled",
            "rag_synthetic_fallback": "enabled" if settings.rag_demo_synthetic_fallback else "disabled",
            "rag_query_strategy_mode": settings.rag_query_strategy_mode,
        },
    }


@app.get("/", include_in_schema=False)
async def root_viewer_redirect():
    # 程式筆記：8017 的根路徑是給人直接打開看的入口，不承擔 API 合約；
    # 因此只轉到醫工 demo viewer，避免瀏覽器顯示 FastAPI 404 造成誤判。
    return RedirectResponse(url="/demo/latest", status_code=307)


@app.post("/api/intake")
async def intake(request: Request, background_tasks: BackgroundTasks):
    try:
        raw_payload = safe_json_loads(await request.body())
        if (
            str(raw_payload.get("demo_mode") or "").strip().lower() in {"demo_fixture", "deterministic_demo"}
            and not settings.demo_fixture_mode
        ):
            # ponytail: Reject an explicit Demo request instead of silently sending it through live RAG as a misleading yellow light.
            return _error_response("demo_fixture_disabled")
        payload = _parse_payload(raw_payload)
    except SafeIntakeError as exc:
        return _error_response(exc.error_code, message=exc.message)

    normalized = payload.model_dump()
    hash_value = payload_hash(normalized)
    client_session_id = payload.session_id.strip()
    existing_rows = db.find_by_client_session(client_session_id)
    for row in existing_rows:
        if row["payload_hash"] != hash_value:
            return _error_response("client_session_conflict")
        if row["response_json"]:
            try:
                cached = json.loads(row["response_json"])
            except Exception:
                cached = None
            if isinstance(cached, dict) and cached:
                error_code = cached.get("error_code")
                status_code = get_error(error_code).http_status if error_code else 200
                return JSONResponse(status_code=status_code, content=cached)

    session = db.create_session(
        client_session_id=client_session_id,
        payload_hash_value=hash_value,
        schema_version=payload.schema_version,
        source=payload.source,
        input_origin=payload.input_origin,
        zero_disk_image_io=payload.zero_disk_image_io,
        patient_uid_ref_value=patient_uid_ref(payload.patient_uid),
    )
    session_id = session["id"]
    correlation_id = session["correlation_id"]

    clinical = map_payload_to_clinical(payload)
    clinical = await _maybe_enrich_clinical_parse(payload, clinical)
    db.add_event(session_id, "clinical_parse", "info", "parsed", "Clinical SOAP mapped to Dx/Tx/Hx.", None, {"missing_field_count": len(clinical.missing_fields)})
    db.update_session(
        session_id,
        status="parsed",
        dx=clinical.dx,
        tx=clinical.tx,
        hx=clinical.hx,
        parse_confidence=clinical.parse_confidence,
    )

    demo_ebm_hits = build_demo_ebm_fixture(payload, clinical, enabled=settings.demo_fixture_mode)
    if demo_ebm_hits:
        db.add_event(
            session_id,
            "rag",
            "info",
            "demo_fixture",
            "已命中 Demo Fixture；本次跳過 live RAG 呼叫，改用可重現展示證據。",
            None,
            {"fixture_id": demo_ebm_hits.get("demo_fixture_id")},
        )
        adjudication = deterministic_adjudication(clinical, demo_ebm_hits)
        adjudication["status"] = "demo_fixture"
        response = build_success_response(
            session_id=session_id,
            client_session_id=client_session_id,
            correlation_id=correlation_id,
            clinical=clinical,
            ebm_hits=demo_ebm_hits,
            adjudication=adjudication,
        )
        db.update_session(
            session_id,
            status=response["status"],
            completed_at=utc_now(),
            rag_response_json=json.dumps(demo_ebm_hits, ensure_ascii=False, sort_keys=True),
            rag_query_id=response["ebm"].get("rag_query_id"),
            light_color=response["final_gate"].get("light_color"),
            final_score=float(adjudication.get("evidence_support_score") or 0),
            response_json=json.dumps(response, ensure_ascii=False, sort_keys=True),
        )
        db.add_event(
            session_id,
            "final_gate",
            "info",
            "completed",
            "Deterministic demo final gate evaluated.",
            None,
            {"evidence_backed": response["final_gate"].get("evidence_backed")},
        )
        return response

    rag_request = build_rag_check_request(clinical)
    db.update_session(session_id, status="rag_pending", rag_request_json=json.dumps(rag_request, ensure_ascii=False, sort_keys=True))
    db.add_event(session_id, "rag", "info", "pending", "RAG /check request prepared.", None, {"top_k": rag_request.get("top_k")})
    rag_result, rag_request = await call_rag_check(clinical)
    db.update_session(session_id, status="rag_pending", rag_request_json=json.dumps(rag_request, ensure_ascii=False, sort_keys=True))
    if not rag_result.ok:
        response = build_degraded_response(
            session_id=session_id,
            client_session_id=client_session_id,
            correlation_id=correlation_id,
            clinical=clinical,
            error_code=rag_result.error_code or "rag_unavailable",
            rag_payload=rag_result.payload,
        )
        db.update_session(
            session_id,
            status=response["status"],
            completed_at=utc_now(),
            error_code=response.get("error_code"),
            degraded_reason=response["final_gate"]["reason"],
            rag_response_json=json.dumps(rag_result.payload, ensure_ascii=False, sort_keys=True),
            light_color=response["final_gate"]["light_color"],
            response_json=json.dumps(response, ensure_ascii=False, sort_keys=True),
        )
        db.add_event(session_id, "rag", "warning", "degraded", response["final_gate"]["reason"], response.get("error_code"), {"retryable": response.get("retryable", False)})
        if rag_result.error_code == "rag_timeout":
            background_tasks.add_task(
                _complete_rag_background,
                session_id=session_id,
                client_session_id=client_session_id,
                correlation_id=correlation_id,
                clinical=clinical,
            )
            db.add_event(session_id, "rag", "info", "background_scheduled", "Background RAG completion scheduled.", None, {"timeout_seconds": settings.rag_background_timeout_seconds})
        return JSONResponse(status_code=get_error(rag_result.error_code or "rag_unavailable").http_status, content=response)

    db.add_event(session_id, "rag", "info", "completed", "RAG /check returned ebm_hits.", None, {"query_id": rag_result.payload.get("query_id")})
    adjudication = await adjudicate(clinical, rag_result.payload)
    response = build_success_response(
        session_id=session_id,
        client_session_id=client_session_id,
        correlation_id=correlation_id,
        clinical=clinical,
        ebm_hits=rag_result.payload,
        adjudication=adjudication,
    )
    db.update_session(
        session_id,
        status=response["status"],
        completed_at=utc_now(),
        rag_response_json=json.dumps(rag_result.payload, ensure_ascii=False, sort_keys=True),
        rag_query_id=response["ebm"].get("rag_query_id"),
        light_color=response["final_gate"].get("light_color"),
        final_score=float(adjudication.get("evidence_support_score") or 0),
        response_json=json.dumps(response, ensure_ascii=False, sort_keys=True),
    )
    db.add_event(session_id, "final_gate", "info", "completed", "Final deterministic gate evaluated.", None, {"evidence_backed": response["final_gate"].get("evidence_backed")})
    return response


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    row = db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    response = {}
    if row.get("response_json"):
        try:
            response = json.loads(row["response_json"])
        except Exception:
            response = {}
    return {"ok": True, "session": row, "response": response}


@app.get("/api/client-sessions/{client_session_id}")
async def get_client_session(client_session_id: str):
    row = db.latest_by_client_session(client_session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Client session not found")
    response = {}
    if row.get("response_json"):
        try:
            response = json.loads(row["response_json"])
        except Exception:
            response = {}
    return {"ok": True, "session": row, "response": response}


@app.get("/api/sessions/{session_id}/events")
async def get_events(session_id: str):
    row = db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "session_id": session_id, "events": db.list_events(session_id)}


@app.get("/api/demo/latest")
async def latest_demo_json():
    row = db.latest_session()
    if not row:
        return {"ok": False, "status": "empty", "message": "No sessions yet."}
    response = {}
    if row.get("response_json"):
        try:
            response = json.loads(row["response_json"])
        except Exception:
            response = {}
    return {"ok": True, "session": row, "response": response, "events": db.list_events(row["id"])}


@app.get("/demo/latest", response_class=HTMLResponse)
async def latest_demo_viewer():
    # 程式筆記：Python package 位於 server_app/，但 static/ 保留在
    # llmxx-server 專案根目錄；統一使用 settings.STATIC_DIR，避免 package
    # 化後 viewer 找錯位置。
    html_path = STATIC_DIR / "demo_latest.html"
    if not html_path.exists():
        return HTMLResponse("<!doctype html><title>RootMedicals Demo</title><p>Demo viewer missing.</p>", status_code=500)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

