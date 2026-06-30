# 檔案路徑: rootmedicals-a/ebm-rag/lava/adapter/anthropic.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/adapter/anthropic.py
# Timestamp: 2026-06-09
# Version: v0.3
# Description: Anthropic Claude Adapter。supports_chat=True；supports_embedding=False。
#              model list 為靜態清單；使用 anthropic-version header。
# ----------------------------------------------------------------------------------------------------

import httpx
from lava.adapter.base import BaseLavaAdapter

_BASE = "https://api.anthropic.com/v1"
_STATIC_MODELS = ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-3-5"]

class AnthropicAdapter(BaseLavaAdapter):
    provider = "anthropic"
    supports_chat = True
    supports_embedding = False

    def _headers(self, api_key: str) -> dict:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }

    async def fetch_models(self, api_key: str):
        return _STATIC_MODELS

    async def verify_chat(self, api_key: str, model_id: str) -> dict:
        try:
            result = await self.chat(api_key, model_id, [{"role": "user", "content": "ping"}], max_tokens=5)
            return {"ok": True, "model": model_id, "provider": self.provider, "sample": result.get("content", "")}
        except Exception as e:
            return {"ok": False, "error": self.safe_error(e, api_key)}

    async def chat(self, api_key: str, model_id: str, messages: list, temperature: float = 0.1, max_tokens: int = 2048) -> dict:
        try:
            payload = {"model": model_id, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            async with httpx.AsyncClient(timeout=60) as client:
                async def do_request():
                    response = await client.post(f"{_BASE}/messages", headers=self._headers(api_key), json=payload)
                    response.raise_for_status()
                    return response
                r = await self.request_with_retries(do_request, attempts=3)
                data = r.json()
                content = data["content"][0]["text"]
                return {"content": content, "model": model_id, "provider": self.provider}
        except Exception as e:
            raise RuntimeError(self.safe_error(e, api_key)) from None
