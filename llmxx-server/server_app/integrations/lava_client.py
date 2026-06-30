# 檔案路徑: rootmedicals-a/llmxx-server/server_app/integrations/lava_client.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.2-LAVA adapter 交接註解
# 模組定位:
#   llmxx-server 呼叫 LAVA task 的最小 HTTP adapter。LAVA 是可插拔的 LLM 控制面，
#   不是 final gate 本身；任何 LAVA 失敗都應降級為 deterministic fallback。
# 主要責任:
#   1. 將 task_id 與 payload POST 到 ebm-rag 的 /api/lava/tasks/{task_id}/invoke。
#   2. 把未綁定、timeout、HTTP 錯誤、非 JSON result 轉成 LavaInvokeResult。
#   3. 保持 adapter 簡單，避免把 task prompt 或臨床決策邏輯塞在 server 端。
# 呼叫來源:
#   api.main 用於 clinical_soap_parse；core.adjudicator 用於 llmaaj_adjudicate。
# 輸入契約:
#   task_id 必須是 LAVA 已註冊任務；payload 必須可 JSON 序列化。
# 輸出契約:
#   ok=True 時 result 為 dict；ok=False 時 error_code 可供上層標記 fallback。
# 安全邊界:
#   adapter 不接觸 API key，也不回傳 LAVA 連線細節給醫師端。connection_id 目前固定由 RAG/LAVA 層解析。
# 維護提醒:
#   若未來支援多 connection_id，應從設定或 task binding 讀取，不要在 intake payload 暴露 key 或 connection secret。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from ..infra.settings import settings


class LavaInvokeResult:
    def __init__(self, ok: bool, result: dict[str, Any], error_code: str | None = None):
        self.ok = ok
        self.result = result
        self.error_code = error_code


def _post_task(task_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    # LAVA task 呼叫保持短 timeout：它是輔助判讀，不應拖住醫師端主流程。
    # timeout 後上層會採 deterministic fallback，而不是讓 Ctrl+Alt+G 無限等待。
    url = f"{settings.lava_task_base_url.rstrip('/')}/{task_id}/invoke"
    body = {"connection_id": 0, "payload": payload}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=settings.lava_timeout_seconds) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            return resp.status, parsed if isinstance(parsed, dict) else {"error": "non_object_json"}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            parsed = {"error": "http_error_non_json"}
        return exc.code, parsed if isinstance(parsed, dict) else {"error": "http_error_non_object"}


async def invoke_lava_task(task_id: str, payload: dict[str, Any]) -> LavaInvokeResult:
    # 統一把 LAVA 不可用視為 lava_unbound，讓上層可以用同一條 fallback 路徑。
    # 這裡不要拋例外到 API route，否則單一任務未綁定會讓整筆 intake 失敗。
    try:
        status_code, response = await asyncio.to_thread(_post_task, task_id, payload)
    except TimeoutError:
        return LavaInvokeResult(False, {"status": "degraded", "error": "lava timeout"}, "lava_unbound")
    except Exception as exc:
        return LavaInvokeResult(False, {"status": "degraded", "error": str(exc)[:160]}, "lava_unbound")
    if status_code == 409:
        return LavaInvokeResult(False, response, "lava_unbound")
    if status_code >= 400:
        return LavaInvokeResult(False, response, "lava_unbound")
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        return LavaInvokeResult(False, response, "llm_malformed_json")
    return LavaInvokeResult(True, result, None)

