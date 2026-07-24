# 模組定位: LAVA provider adapters 的 capability 與 transport 基底。
# 主要責任: 定義 chat/embed/vision 契約、retry 規則、影像 bounds 與錯誤清洗。
# 呼叫來源: provider-specific adapters、LAVA task executors 與 contract tests。
# 輸入契約: verified connection、bounded prompt/messages 與最多 3 張 PNG/JPEG base64 影像。
# 輸出契約: provider-neutral model/chat/embedding 結果或不含 secret 的可診斷錯誤。
# 安全邊界: API key 不得進入 error；影像 MIME、base64 與 5 MiB 上限必須先驗證。
# 維護提醒: 新 provider 必須明確宣告 capability，不能以 chat verify 冒充 vision/embed verify。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/adapter/base.py
# Timestamp: 2026-06-09
# Version: v0.5
# Description: LAVA Adapter 抽象基類。定義 fetch_models / verify_chat / verify_embedding / chat / embed 介面契約。
#              所有 Provider adapter 必須繼承此類；錯誤訊息必須清洗 API key。
# ----------------------------------------------------------------------------------------------------

from abc import ABC, abstractmethod
from typing import List, Dict
import asyncio
import base64
import binascii
import re

import httpx

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

class BaseLavaAdapter(ABC):
    provider: str
    supports_chat: bool = True
    supports_embedding: bool = False
    supports_vision: bool = False

    _VISION_MIME_TYPES = {"image/png", "image/jpeg"}
    _MAX_VISION_IMAGES = 3
    _MAX_VISION_IMAGE_BYTES = 5 * 1024 * 1024

    @abstractmethod
    async def fetch_models(self, api_key: str) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    async def verify_chat(self, api_key: str, model_id: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    async def chat(self, api_key: str, model_id: str, messages: List[Dict],
                   temperature: float = 0.1, max_tokens: int = 2048) -> Dict:
        raise NotImplementedError

    async def vision(self, api_key: str, model_id: str, prompt: str, images: List[Dict],
                     temperature: float = 0.0, max_tokens: int = 2048) -> Dict:
        raise NotImplementedError("This provider does not support vision")

    async def embed(self, api_key: str, model_id: str, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError("This provider does not support embedding")

    def _status_code_from_error(self, error: Exception) -> int | None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        message = str(error)
        match = re.search(r"\b(429|500|502|503|504)\b", message)
        if match:
            return int(match.group(1))
        return None

    def _is_retryable_error(self, error: Exception) -> bool:
        if isinstance(error, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
            return True
        status_code = self._status_code_from_error(error)
        return status_code in _RETRYABLE_STATUS_CODES

    async def request_with_retries(self, request_callable, attempts: int = 3):
        safe_attempts = max(1, min(int(attempts or 1), 4))
        last_error = None
        for attempt_index in range(safe_attempts):
            try:
                return await request_callable()
            except Exception as error:
                last_error = error
                if attempt_index >= safe_attempts - 1 or not self._is_retryable_error(error):
                    raise
                await asyncio.sleep(0.75 * (2 ** attempt_index))
        raise last_error

    def safe_error(self, error: Exception, api_key: str = "") -> str:
        message = str(error)
        if api_key:
            message = message.replace(api_key, "[REDACTED_API_KEY]")
        message = re.sub(r"([?&]key=)[^'\"\\s]+", r"\1[REDACTED_API_KEY]", message)
        message = re.sub(r"(Authorization:\\s*Bearer\\s+)[A-Za-z0-9._\\-]+", r"\1[REDACTED_API_KEY]", message)
        message = re.sub(r"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]+", "[REDACTED_IMAGE]", message)
        status_code = self._status_code_from_error(error)
        if status_code == 429:
            return "provider_rate_limited: upstream provider returned HTTP 429"
        if status_code in {500, 502, 503, 504}:
            return f"provider_unavailable: upstream provider returned HTTP {status_code}"
        if isinstance(error, httpx.TimeoutException) or "timed out" in message.lower() or "timeout" in message.lower():
            return "provider_timeout: upstream provider request timed out"
        message = re.sub(r"https?://[^'\"\\s]+", "[REDACTED_URL]", message)
        return message[:500]

    @classmethod
    def validate_vision_images(cls, images: List[Dict]) -> List[Dict]:
        if not isinstance(images, list) or not 1 <= len(images) <= cls._MAX_VISION_IMAGES:
            raise ValueError(f"images must contain 1 to {cls._MAX_VISION_IMAGES} items")
        normalized = []
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                raise ValueError(f"images[{index}] must be an object")
            mime_type = str(image.get("mime_type") or "").strip().lower()
            if mime_type not in cls._VISION_MIME_TYPES:
                raise ValueError(f"images[{index}].mime_type must be image/png or image/jpeg")
            raw = image.get("data")
            if raw is None:
                encoded = image.get("image_b64")
                if not isinstance(encoded, str):
                    raise ValueError(f"images[{index}] requires data bytes or image_b64")
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise ValueError(f"images[{index}].image_b64 is invalid base64") from error
            if not isinstance(raw, (bytes, bytearray)):
                raise ValueError(f"images[{index}].data must be bytes")
            raw = bytes(raw)
            if not raw or len(raw) > cls._MAX_VISION_IMAGE_BYTES:
                raise ValueError(
                    f"images[{index}] decoded size must be between 1 and {cls._MAX_VISION_IMAGE_BYTES} bytes"
                )
            if mime_type == "image/png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"images[{index}] is not a valid PNG payload")
            if mime_type == "image/jpeg" and not (raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")):
                raise ValueError(f"images[{index}] is not a valid JPEG payload")
            normalized.append({"mime_type": mime_type, "data": raw})
        return normalized

    async def verify_vision(self, api_key: str, model_id: str) -> Dict:
        if not self.supports_vision:
            return {"ok": False, "error": "Provider does not support vision"}
        # A public 1x1 PNG keeps verification independent of user screenshots.
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        try:
            result = await self.vision(
                api_key,
                model_id,
                "Reply with the single word OK after inspecting this harmless test image.",
                [{"mime_type": "image/png", "data": image}],
                # Gemini 2.5 may spend part of the output budget on internal
                # reasoning before emitting the requested text.
                max_tokens=1024,
            )
            if not str(result.get("content") or "").strip():
                return {"ok": False, "error": "Vision endpoint returned empty content"}
            return {
                "ok": True,
                "model": model_id,
                "provider": self.provider,
                "capability": "vision",
            }
        except Exception as error:
            return {"ok": False, "error": self.safe_error(error, api_key)}

    async def verify_embedding(self, api_key: str, model_id: str) -> Dict:
        if not self.supports_embedding:
            return {"ok": False, "error": "Provider does not support embedding"}
        try:
            vectors = await self.embed(api_key, model_id, ["rootmedicals embedding verification"])
            if not isinstance(vectors, list) or len(vectors) != 1:
                return {"ok": False, "error": "Embedding endpoint returned invalid vector count"}
            vector = vectors[0]
            if not isinstance(vector, list) or len(vector) == 0:
                return {"ok": False, "error": "Embedding endpoint returned empty vector"}
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in vector):
                return {"ok": False, "error": "Embedding vector contains non-numeric values"}
            return {
                "ok": True,
                "model": model_id,
                "provider": self.provider,
                "capability": "embedding",
                "dim": len(vector)
            }
        except Exception as e:
            return {"ok": False, "error": self.safe_error(e, api_key)}
