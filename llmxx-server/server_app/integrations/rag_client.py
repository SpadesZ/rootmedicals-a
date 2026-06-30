# 檔案路徑: rootmedicals-a/llmxx-server/server_app/integrations/rag_client.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.2-RAG adapter 交接註解
# 模組定位:
#   llmxx-server 與 ebm-rag 之間的 HTTP adapter。這裡只負責建 request、呼叫 /rag/check、
#   以及把 transport/provider 失敗轉成可被 final gate 降級的錯誤碼。
# 主要責任:
#   1. 用 ICD code、normalized diagnosis、A/P/Hx 組成 RAG 查詢請求。
#   2. 控制 top_k、query_decomposition_mode 與 demo synthetic fallback filter。
#   3. 把 timeout、RAG 未就緒、HTTP 5xx、malformed payload 分流成明確 error_code。
# 呼叫來源:
#   server_app.api.main.process_intake() 在非 Demo Fixture 路徑呼叫 call_rag_check()。
# 輸入契約:
#   ClinicalParse.dx 至少要有值；ICD 欄位若有會優先送給 RAG 作疾病分類錨點。
# 輸出契約:
#   回傳 RagResult 與原始 request_body；失敗時仍保留 request_body 供 event log/diagnostics 追查。
# 安全邊界:
#   adapter 不決定燈號，不補假 evidence，不吞掉 provider 錯誤；它只提供 response_builder 可降級的狀態。
# 維護提醒:
#   新增 RAG 欄位時要同步確認 ebm-rag /api/v1/rag/check schema，
#   並測 live RAG、RAG timeout、RAG not ready、malformed JSON 四種情境。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request
from typing import Any

from ..infra.errors import get_error
from ..contracts.schemas import ClinicalParse
from ..infra.settings import settings


class RagResult:
    def __init__(self, ok: bool, payload: dict[str, Any], error_code: str | None = None):
        self.ok = ok
        self.payload = payload
        self.error_code = error_code


def _post_json(url: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    # 這裡刻意用標準庫 urllib，避免 server 端為單一 POST adapter 再引入額外 async HTTP 相依。
    # call_rag_check 會用 asyncio.to_thread 包住，避免阻塞 FastAPI event loop。
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(parsed, dict):
                return resp.status, {"error": "non_object_json"}
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            parsed = {"error": "http_error_non_json", "detail": str(exc)}
        return exc.code, parsed if isinstance(parsed, dict) else {"error": "http_error_non_object"}


def _validate_rag_payload(payload: dict[str, Any]) -> bool:
    # 只做最低限度 contract 檢查。完整 evidence 是否可信交給 response_builder；
    # adapter 層不應根據欄位缺失直接推燈號。
    if not isinstance(payload, dict):
        return False
    if "query_id" not in payload and "light_color" not in payload and "rag_comments" not in payload:
        return False
    if payload.get("rag_comments") is not None and not isinstance(payload.get("rag_comments"), list):
        return False
    return True


def build_rag_check_request(clinical: ClinicalParse, top_k: int | None = None) -> dict[str, Any]:
    # RAG 查詢優先使用 ICD 與 normalized diagnosis，避免只靠 OCR 出來的 A 欄自由文字。
    # A/P/Hx 仍保留，讓 RAG 可以檢查治療主張與病史情境。
    clamped_top_k = max(1, min(int(top_k or settings.default_top_k), settings.max_top_k))
    request_body: dict[str, Any] = {
        "dx": clinical.dx,
        "tx": clinical.tx or None,
        "hx": clinical.hx or None,
        "age": clinical.age,
        "sex": clinical.sex,
        "labs": clinical.labs,
        "icd_code": clinical.icd10_code or clinical.icd_code or None,
        "icd10_code": clinical.icd10_code or clinical.icd_code or None,
        "diagnosis_label": clinical.diagnosis_label or clinical.icd_label or clinical.normalized_diagnosis or None,
        "normalized_diagnosis": clinical.normalized_diagnosis or clinical.dx or None,
        "dx_text": clinical.dx_text or None,
        "top_k": clamped_top_k,
    }
    filters: dict[str, Any] = {}
    if settings.rag_demo_synthetic_fallback:
        # synthetic fallback 是 live RAG 的展示輔助，不是 final gate 的通行證。
        # 最後仍要靠 response_builder 檢查來源、claim support 與 ICD gate。
        filters["demo_synthetic_fallback"] = True
    strategy_mode = str(settings.rag_query_strategy_mode or "").strip().lower()
    if strategy_mode in {"deterministic", "llm_assisted"}:
        filters["query_decomposition_mode"] = strategy_mode
    if filters:
        request_body["filters"] = filters
    return request_body


async def call_rag_check(
    clinical: ClinicalParse,
    top_k: int | None = None,
    timeout_seconds: float | None = None,
) -> tuple[RagResult, dict[str, Any]]:
    request_body = build_rag_check_request(clinical, top_k=top_k)
    if not clinical.dx:
        # 沒有 Dx 時不要打 RAG；這種資料缺口在醫師端應顯示 review，而不是查出無關 evidence。
        return RagResult(False, {"status": "not_evaluable", "error": "dx is missing", "light_color": "yellow"}, "invalid_payload"), request_body
    try:
        timeout = settings.rag_timeout_seconds if timeout_seconds is None else max(0.5, float(timeout_seconds))
        status_code, payload = await asyncio.to_thread(_post_json, settings.rag_check_url, request_body, timeout)
    except socket.timeout:
        # timeout 是可預期的外部狀態，回 rag_timeout 讓 final gate 黃燈/人工確認。
        return RagResult(False, {"status": "degraded", "error": "rag timeout"}, "rag_timeout"), request_body
    except TimeoutError:
        return RagResult(False, {"status": "degraded", "error": "rag timeout"}, "rag_timeout"), request_body
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        return RagResult(False, {"status": "degraded", "error": str(exc)[:160]}, "rag_unavailable"), request_body
    except Exception as exc:
        return RagResult(False, {"status": "not_evaluable", "error": str(exc)[:160]}, "rag_malformed_json"), request_body

    if status_code == 409:
        return RagResult(False, payload, "rag_not_ready"), request_body
    if status_code >= 500:
        return RagResult(False, payload, "rag_unavailable"), request_body
    if not _validate_rag_payload(payload):
        return RagResult(False, payload, "rag_malformed_json"), request_body
    return RagResult(True, payload, None), request_body

