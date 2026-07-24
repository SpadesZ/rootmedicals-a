# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/server_client.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.2-HTTP client 交接註解
# 模組定位:
#   thin client 到 llmxx-server 的唯一 HTTP adapter。pipeline 只管截圖與 payload，
#   送出、加密、timeout fallback、session polling 都集中在這裡。
# 主要責任:
#   1. POST screenshot payload 到 /api/intake。
#   2. 在 server 尚未即時回覆時產生 pending response，讓醫師端知道系統正在處理。
#   3. 提供 session/latest 查詢給 tray polling 與醫師浮窗更新使用。
# 輸入契約:
#   network_config 來自 config/default_config.json；payload 必須已由 payload.py 組好。
# 輸出契約:
#   回傳 server response 或本機 fallback response，形狀要能被 DoctorAlertWidget 消化。
# 安全邊界:
#   非 localhost 的 HTTP 不允許；正式環境應使用 HTTPS 或 AES envelope。
#   本檔不寫入 payload，也不負責診斷檔遮蔽。
# 維護提醒:
#   改 timeout 行為時要測 Ctrl+Alt+G 的「處理中」提示，避免使用者以為按鍵沒有送出。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urljoin, urlparse

from .encryption import maybe_encrypt_payload


class ServerClient:
    def __init__(self, network_config: Dict[str, Any]):
        self.network_config = network_config

    def _verify_tls(self) -> bool:
        # 預設驗證 TLS 憑證（安全）。只有交付設定明確關閉時才略過，
        # 用於 VM 走自簽憑證 / sslip.io 主機名與憑證不匹配的 demo 環境。
        # 注意：關閉後 PHI 仍走 TLS 加密，但不驗證對端身分，正式環境應改用有效憑證。
        return bool(self.network_config.get("verify_tls", True))

    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # send_enabled 讓 demo/診斷可以只截圖不送出；正式閉環要保持 true。
        if not self.network_config.get("send_enabled", False):
            return {"status": "skipped", "message": "network.send_enabled is false"}

        endpoint = str(self.network_config.get("endpoint_url", "")).strip()
        if not endpoint:
            raise ValueError("network.endpoint_url is empty")
        self._validate_endpoint(endpoint)

        try:
            import httpx
        except Exception as exc:
            raise RuntimeError("httpx is required for server communication") from exc

        # maybe_encrypt_payload 只包送出的 JSON，不改原始 payload；diagnostics 仍使用遮蔽副本。
        outbound = maybe_encrypt_payload(payload, self.network_config)
        timeout_seconds = float(self.network_config.get("timeout_seconds", 12.0) or 12.0)
        with httpx.Client(timeout=timeout_seconds, verify=self._verify_tls()) as client:
            try:
                response = client.post(endpoint, json=outbound)
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {"status": "success", "message": "empty response body"}
            except httpx.TimeoutException:
                return self._timeout_pending_response(payload, timeout_seconds)
            except httpx.HTTPStatusError as exc:
                return self._http_error_response(payload, exc)

    def get_session(self, session_id: str) -> Dict[str, Any]:
        # server session id 與 client session id 都可能被拿來查詢。
        # 先查 server session；若 404，再退到 client-session lookup，支援醫師端 polling。
        session_key = str(session_id or "").strip()
        if not session_key:
            return {"ok": False, "status": "skipped", "message": "session_id is empty"}
        endpoint = self._server_base_url()
        if not endpoint:
            return {"ok": False, "status": "skipped", "message": "network.endpoint_url is empty"}
        try:
            return self._get_json(urljoin(endpoint, f"/api/sessions/{session_key}"))
        except Exception as exc:
            if not self._is_http_404(exc):
                raise
            return self._get_json(urljoin(endpoint, f"/api/client-sessions/{session_key}"))

    def get_latest_demo(self) -> Dict[str, Any]:
        endpoint = self._server_base_url()
        if not endpoint:
            return {"ok": False, "status": "skipped", "message": "network.endpoint_url is empty"}
        return self._get_json(urljoin(endpoint, "/api/demo/latest"))

    def _get_json(self, url: str) -> Dict[str, Any]:
        self._validate_endpoint(url)
        try:
            import httpx
        except Exception as exc:
            raise RuntimeError("httpx is required for server communication") from exc
        timeout_seconds = float(self.network_config.get("timeout_seconds", 12.0) or 12.0)
        with httpx.Client(timeout=timeout_seconds, verify=self._verify_tls()) as client:
            response = client.get(url)
            response.raise_for_status()
            if response.content:
                parsed = response.json()
                return parsed if isinstance(parsed, dict) else {"ok": False, "status": "invalid_json"}
            return {"ok": True, "status": "empty"}

    def _server_base_url(self) -> str:
        endpoint = str(self.network_config.get("endpoint_url", "")).strip()
        if not endpoint:
            return ""
        parsed = urlparse(endpoint)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    def _timeout_pending_response(self, payload: Dict[str, Any], timeout_seconds: float) -> Dict[str, Any]:
        # timeout 不直接視為失敗。server 可能還在跑 RAG/background completion，
        # 所以先回灰燈 pending，讓浮窗顯示「有送出、仍在處理」。
        client_session_id = str(payload.get("session_id") or "").strip()
        soap = payload.get("soap") if isinstance(payload.get("soap"), dict) else {}
        hx = "; ".join(
            item
            for item in (
                f"S: {soap.get('S', '')}".strip(),
                f"O: {soap.get('O', '')}".strip(),
            )
            if len(item) > 3
        )
        return {
            "ok": False,
            "status": "timeout_pending",
            "error_code": "server_response_timeout",
            "retryable": True,
            "session_id": client_session_id,
            "client_session_id": client_session_id,
            "message": f"Server response exceeded {timeout_seconds:.1f}s; polling by client_session_id.",
            "clinical_parse": {
                "dx": str(soap.get("A") or "").strip(),
                "tx": str(soap.get("P") or "").strip(),
                "hx": hx,
                "parse_confidence": 0.0,
            },
            "ebm": {
                "short_comment": "已送出至 EBM，正在等待 RAG / verifier 回覆。",
                "rag_comments": [],
                "alternatives": [],
            },
            "final_gate": {
                "light_color": "gray",
                "display_mode": "pending",
                "evidence_backed": False,
                "reason": "Server response timeout; result may still be processing.",
                "reason_codes": ["server_response_timeout"],
                "hard_fail_reasons": [],
            },
        }

    def _http_error_response(self, payload: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        response = getattr(exc, "response", None)
        status_code = int(getattr(response, "status_code", 0) or 0)
        parsed: Dict[str, Any] = {}
        if response is not None:
            try:
                body = response.json()
                parsed = body if isinstance(body, dict) else {}
            except Exception:
                parsed = {}
        client_session_id = str(payload.get("session_id") or "").strip()
        parsed.setdefault("ok", False)
        parsed.setdefault("status", "failed")
        parsed.setdefault("error_code", "server_http_error")
        parsed.setdefault("retryable", status_code >= 500)
        parsed.setdefault("session_id", client_session_id)
        parsed.setdefault("client_session_id", client_session_id)
        parsed.setdefault("http_status", status_code)
        parsed.setdefault("message", f"Server returned HTTP {status_code}.")
        parsed.setdefault(
            "final_gate",
            {
                "light_color": "red",
                "display_mode": "error",
                "evidence_backed": False,
                "reason": str(parsed.get("message") or f"Server returned HTTP {status_code}."),
                "reason_codes": [str(parsed.get("error_code") or "server_http_error")],
                "hard_fail_reasons": [str(parsed.get("error_code") or "server_http_error")],
            },
        )
        return parsed

    def _is_http_404(self, exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        return int(status_code or 0) == 404

    def _validate_endpoint(self, endpoint: str) -> None:
        # Demo 在本機用 http://127.0.0.1，正式部署才要求 HTTPS。
        # 這個例外只限 localhost，避免不小心把截圖 payload 明文送到外部 HTTP。
        parsed = urlparse(endpoint)
        if parsed.scheme == "https":
            return
        host = parsed.hostname or ""
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme == "http" and host in local_hosts and self.network_config.get("allow_insecure_localhost_http", True):
            return
        raise ValueError("Endpoint must use HTTPS unless it is explicitly allowed localhost HTTP")


