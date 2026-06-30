# 檔案路徑: rootmedicals-a/ebm-rag/lava/adapter/google.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/adapter/google.py
# Timestamp: 2026-06-09
# Version: v0.6
# Description: Google Gemini Adapter。支援 chat 與 embedding。
#              generateContent 用於 chat；embedContent 用於 embedding_dense；回應解析與錯誤訊息皆防禦處理。
# ----------------------------------------------------------------------------------------------------

import httpx
from lava.adapter.base import BaseLavaAdapter

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

_STATIC_MODELS = [
    "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash",
    "gemini-2.0-flash-thinking-exp", "gemini-2.5-pro-preview-06-05",
    "gemini-embedding-001", "text-embedding-004"
]

class GoogleAdapter(BaseLavaAdapter):
    provider = "google"
    supports_chat = True
    supports_embedding = True

    async def fetch_models(self, api_key: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                async def do_request():
                    response = await client.get(f"{_GEMINI_BASE}/models?key={api_key}")
                    response.raise_for_status()
                    return response
                r = await self.request_with_retries(do_request, attempts=2)
                r.raise_for_status()
                data = r.json()
                models = []
                for item in data.get("models", []):
                    model_name = str(item.get("name", "")).replace("models/", "")
                    methods = item.get("supportedGenerationMethods", [])
                    if model_name and ("generateContent" in methods or "embedContent" in methods):
                        models.append(model_name)
                return sorted(set(models)) if models else _STATIC_MODELS
        except Exception:
            return _STATIC_MODELS

    async def verify_chat(self, api_key: str, model_id: str) -> dict:
        try:
            result = await self.chat(api_key, model_id, [{"role": "user", "content": "ping"}], max_tokens=5)
            return {"ok": True, "model": model_id, "provider": self.provider, "sample": result.get("content", "")}
        except Exception as e:
            return {"ok": False, "error": self.safe_error(e, api_key)}

    async def chat(self, api_key: str, model_id: str, messages: list, temperature: float = 0.1, max_tokens: int = 2048) -> dict:
        try:
            contents = [{"role": ("user" if m["role"] == "user" else "model"), "parts": [{"text": m["content"]}]} for m in messages]
            payload = {
                "contents": contents,
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}
            }
            async with httpx.AsyncClient(timeout=60) as client:
                async def do_request():
                    response = await client.post(
                        f"{_GEMINI_BASE}/models/{model_id}:generateContent?key={api_key}",
                        json=payload
                    )
                    response.raise_for_status()
                    return response
                r = await self.request_with_retries(do_request, attempts=3)
                data = r.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError(f"Gemini response has no candidates: {data.get('promptFeedback')}")
                first_candidate = candidates[0]
                parts = first_candidate.get("content", {}).get("parts", [])
                text_parts = [str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("text")]
                content = "\n".join(text_parts).strip()
                if not content:
                    finish_reason = first_candidate.get("finishReason", "unknown")
                    raise ValueError(f"Gemini response has no text parts; finishReason={finish_reason}")
                return {"content": content, "model": model_id, "provider": self.provider}
        except Exception as e:
            raise RuntimeError(self.safe_error(e, api_key)) from None

    async def embed(self, api_key: str, model_id: str, texts: list) -> list:
        try:
            if not isinstance(texts, list):
                raise ValueError("texts must be a list")
            vectors = []
            async with httpx.AsyncClient(timeout=60) as client:
                for text in texts:
                    if not isinstance(text, str):
                        raise ValueError("all embedding inputs must be strings")
                    payload = {
                        "model": f"models/{model_id}",
                        "content": {
                            "parts": [
                                {"text": text}
                            ]
                        }
                    }
                    async def do_request():
                        response = await client.post(
                            f"{_GEMINI_BASE}/models/{model_id}:embedContent?key={api_key}",
                            json=payload
                        )
                        response.raise_for_status()
                        return response
                    r = await self.request_with_retries(do_request, attempts=3)
                    data = r.json()
                    vector = data.get("embedding", {}).get("values", [])
                    vectors.append(vector)
            return vectors
        except Exception as e:
            raise RuntimeError(self.safe_error(e, api_key)) from None
