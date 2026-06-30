# 檔案路徑: rootmedicals-a/llmxx-server/server_app/infra/errors.py
# 產生時間: 2026-06-18 11:40 +08:00
# 版本: v0.2
# 模組定位:
#   llmxx-server 的 error code registry。API route、安全檢查、RAG/LAVA adapter 會用這裡的
#   ErrorSpec 產生一致的 HTTP status、retryable 與回應狀態。
# 主要責任:
#   1. 集中管理可預期錯誤碼。
#   2. 區分 hard failure、degraded、not_evaluable 與 retryable。
#   3. 避免各層自行拼錯誤訊息，造成醫師端與 demo viewer 顯示不一致。
# 維護提醒:
#   - 新增 error_code 時，請同步檢查 doctor_alert_widget 的中文原因顯示。
#   - retryable=True 代表稍後重試可能改善，不代表可以升級燈號。
# 驗證方式:
#   - 安全拒收與 RAG timeout 都應回 registry 中定義的 status/message。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    http_status: int
    status: str
    retryable: bool
    message: str


ERRORS: dict[str, ErrorSpec] = {
    # 4xx 類：payload 或安全契約錯誤，client 需要修正後再送。
    "invalid_payload": ErrorSpec("invalid_payload", 400, "failed", False, "Payload is invalid."),
    "unsupported_schema_version": ErrorSpec("unsupported_schema_version", 400, "failed", False, "Schema version is not supported."),
    "diagnostics_wrapper_rejected": ErrorSpec("diagnostics_wrapper_rejected", 400, "failed", False, "Diagnostics/debug payloads are not accepted."),
    "image_payload_rejected": ErrorSpec("image_payload_rejected", 400, "failed", False, "Image payloads are not accepted."),
    "aes_envelope_malformed": ErrorSpec("aes_envelope_malformed", 400, "failed", False, "AES envelope is malformed."),
    "aes_key_unavailable": ErrorSpec("aes_key_unavailable", 503, "failed", True, "AES key is unavailable."),
    "client_session_conflict": ErrorSpec("client_session_conflict", 409, "failed", False, "client_session_id conflicts with a different payload."),
    # 202 類：系統安全降級。醫師端應顯示 review，不可當成 evidence-backed。
    "rag_not_ready": ErrorSpec("rag_not_ready", 202, "not_evaluable", True, "RAG is not ready."),
    "rag_timeout": ErrorSpec("rag_timeout", 202, "degraded", True, "RAG request timed out."),
    "rag_unavailable": ErrorSpec("rag_unavailable", 202, "degraded", True, "RAG is unavailable."),
    "rag_malformed_json": ErrorSpec("rag_malformed_json", 202, "not_evaluable", True, "RAG response is malformed."),
    "lava_unbound": ErrorSpec("lava_unbound", 202, "degraded", True, "Optional LAVA task is unbound."),
    "llm_malformed_json": ErrorSpec("llm_malformed_json", 202, "degraded", True, "LLM task returned malformed JSON."),
}


def get_error(code: str) -> ErrorSpec:
    return ERRORS.get(code, ErrorSpec(code, 500, "failed", True, "Unexpected server error."))

