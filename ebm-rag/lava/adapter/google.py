# 模組定位: Google Gemini 的 LAVA chat/vision/embedding adapter。
# 主要責任: 建立 Gemini REST payload、解析 response 並沿用 BaseLavaAdapter safety contract。
# 呼叫來源: LAVA verification、topic vision planner、composer 與 embedding task。
# 輸入契約: Google model id、secret API key、文字 messages 與已驗證 bounded images。
# 輸出契約: 統一 chat/embed result，生成結果保留 provider finish_reason 供 bounded fallback 判斷。
# 安全邊界: key 僅放 request query/header，不得出現在 response、log 或 exception text。
# 維護提醒: Gemini API schema/capability 改版時先補 fixture test，不用靜態 model 名單代替 verify。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/adapter/google.py
# Timestamp: 2026-06-09
# Version: v0.6
# Description: Google Gemini Adapter。支援 chat 與 embedding。
#              generateContent 用於 chat；embedContent 用於 embedding_dense；回應解析與錯誤訊息皆防禦處理。
# ----------------------------------------------------------------------------------------------------

import base64

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
    supports_vision = True

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

    async def chat(
        self, api_key: str, model_id: str, messages: list,
        temperature: float = 0.1, max_tokens: int = 2048,
        response_mime_type: str | None = None,
    ) -> dict:
        try:
            contents = [{"role": ("user" if m["role"] == "user" else "model"), "parts": [{"text": m["content"]}]} for m in messages]
            generation_config = {"temperature": temperature, "maxOutputTokens": max_tokens}
            if response_mime_type:
                # ponytail: JSON mode is opt-in so verification and non-structured chat callers keep their plain-text contract.
                generation_config["responseMimeType"] = response_mime_type
            payload = {
                "contents": contents,
                "generationConfig": generation_config,
            }
            async with httpx.AsyncClient(timeout=60) as client:
                for empty_stop_attempt in range(2):
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
                    text_parts = [
                        str(part.get("text", "")) for part in parts
                        if isinstance(part, dict) and part.get("text")
                    ]
                    content = "\n".join(text_parts).strip()
                    finish_reason = first_candidate.get("finishReason", "unknown")
                    if content:
                        return {
                            "content": content,
                            "model": model_id,
                            "provider": self.provider,
                            "finish_reason": finish_reason,
                        }
                    # ponytail: retry exactly one provider-empty STOP; broader retries belong in a durable job queue.
                    if str(finish_reason).upper() == "STOP" and empty_stop_attempt == 0:
                        continue
                    raise ValueError(f"Gemini response has no text parts; finishReason={finish_reason}")
        except Exception as e:
            raise RuntimeError(self.safe_error(e, api_key)) from None

    async def vision(self, api_key: str, model_id: str, prompt: str, images: list,
                     temperature: float = 0.0, max_tokens: int = 2048) -> dict:
        try:
            normalized = self.validate_vision_images(images)
            parts = [{"text": str(prompt or "")}]
            for image in normalized:
                parts.append({
                    "inline_data": {
                        "mime_type": image["mime_type"],
                        "data": base64.b64encode(image["data"]).decode("ascii"),
                    }
                })
            payload = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            }
            async with httpx.AsyncClient(timeout=60) as client:
                async def do_request():
                    response = await client.post(
                        f"{_GEMINI_BASE}/models/{model_id}:generateContent?key={api_key}",
                        json=payload,
                    )
                    response.raise_for_status()
                    return response
                response = await self.request_with_retries(do_request, attempts=3)
                data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("Gemini vision response has no candidates")
            text_parts = [
                str(part.get("text", ""))
                for part in candidates[0].get("content", {}).get("parts", [])
                if isinstance(part, dict) and part.get("text")
            ]
            content = "\n".join(text_parts).strip()
            if not content:
                finish_reason = candidates[0].get("finishReason", "unknown")
                if str(finish_reason).upper() == "MAX_TOKENS":
                    return {
                        "content": "",
                        "model": model_id,
                        "provider": self.provider,
                        "finish_reason": finish_reason,
                    }
                raise ValueError(f"Gemini vision response has no text parts; finishReason={finish_reason}")
            return {
                "content": content,
                "model": model_id,
                "provider": self.provider,
                "finish_reason": candidates[0].get("finishReason"),
            }
        except Exception as error:
            raise RuntimeError(self.safe_error(error, api_key)) from None

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
