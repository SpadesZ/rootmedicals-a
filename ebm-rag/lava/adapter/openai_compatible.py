# 模組定位: OpenAI-compatible providers 的 LAVA chat/vision/embedding adapter。
# 主要責任: 建立標準相容 payload、解析 response 並執行 shared retry/error sanitization。
# 呼叫來源: LAVA verification、Topic planner/composer 與既有 RAG tasks。
# 輸入契約: provider base URL、model id、secret bearer token、messages 與 bounded images。
# 輸出契約: provider-neutral chat/embed result，生成結果保留 finish_reason，或 sanitized error。
# 安全邊界: Authorization value 不得回傳/記錄；影像必須先通過 BaseLavaAdapter bounds。
# 維護提醒: provider 方言若偏離相容 API，應在該 provider 分支處理，不放寬全域 schema gate。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/adapter/openai_compatible.py
# Timestamp: 2026-06-09
# Version: v0.4
# Description: OpenAI-Compatible Adapter。支援 OpenAI / xAI / DeepSeek / Mistral / OpenRouter。
#              supports_chat=True；supports_embedding=True。embedding 回傳依 index 排序，交由 Core2 驗證契約。
# ----------------------------------------------------------------------------------------------------

import base64

import httpx
from lava.adapter.base import BaseLavaAdapter

class OpenAICompatibleAdapter(BaseLavaAdapter):
    supports_chat = True
    supports_embedding = True
    supports_vision = True

    def __init__(self, provider: str, base_url: str):
        self.provider = provider
        self.base_url = base_url.rstrip("/")

    def _headers(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def fetch_models(self, api_key: str):
        async with httpx.AsyncClient(timeout=15) as client:
            async def do_request():
                response = await client.get(f"{self.base_url}/models", headers=self._headers(api_key))
                response.raise_for_status()
                return response
            r = await self.request_with_retries(do_request, attempts=2)
            data = r.json()
            return [m["id"] for m in data.get("data", [])]

    async def verify_chat(self, api_key: str, model_id: str) -> dict:
        try:
            result = await self.chat(api_key, model_id, [{"role": "user", "content": "ping"}], max_tokens=5)
            return {"ok": True, "model": model_id, "provider": self.provider, "sample": result.get("content", "")}
        except Exception as e:
            return {"ok": False, "error": self.safe_error(e, api_key)}

    async def chat(self, api_key: str, model_id: str, messages: list, temperature: float = 0.1, max_tokens: int = 2048) -> dict:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                payload = {"model": model_id, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
                async def do_request():
                    response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(api_key), json=payload)
                    response.raise_for_status()
                    return response
                r = await self.request_with_retries(do_request, attempts=3)
                data = r.json()
                choice = data["choices"][0]
                content = choice["message"]["content"]
                return {
                    "content": content,
                    "model": model_id,
                    "provider": self.provider,
                    "finish_reason": choice.get("finish_reason"),
                }
        except Exception as e:
            raise RuntimeError(self.safe_error(e, api_key)) from None

    async def vision(self, api_key: str, model_id: str, prompt: str, images: list,
                     temperature: float = 0.0, max_tokens: int = 2048) -> dict:
        try:
            normalized = self.validate_vision_images(images)
            content = [{"type": "text", "text": str(prompt or "")}]
            for image in normalized:
                encoded = base64.b64encode(image["data"]).decode("ascii")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{image['mime_type']};base64,{encoded}"},
                })
            messages = [{"role": "user", "content": content}]
            return await self.chat(
                api_key,
                model_id,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as error:
            raise RuntimeError(self.safe_error(error, api_key)) from None

    async def embed(self, api_key: str, model_id: str, texts: list) -> list:
        try:
            if not isinstance(texts, list):
                raise ValueError("texts must be a list")
            async with httpx.AsyncClient(timeout=60) as client:
                payload = {"model": model_id, "input": texts}
                async def do_request():
                    response = await client.post(f"{self.base_url}/embeddings", headers=self._headers(api_key), json=payload)
                    response.raise_for_status()
                    return response
                r = await self.request_with_retries(do_request, attempts=3)
                data = r.json()
                items = data.get("data", [])
                if all(isinstance(item, dict) and "index" in item for item in items):
                    items = sorted(items, key=lambda item: item["index"])
                return [item["embedding"] for item in items]
        except Exception as e:
            raise RuntimeError(self.safe_error(e, api_key)) from None
