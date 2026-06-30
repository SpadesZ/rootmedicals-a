# 檔案路徑: rootmedicals-a/ebm-rag/lava/adapter/base.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
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
import re

import httpx

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

class BaseLavaAdapter(ABC):
    provider: str
    supports_chat: bool = True
    supports_embedding: bool = False

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
        status_code = self._status_code_from_error(error)
        if status_code == 429:
            return "provider_rate_limited: upstream provider returned HTTP 429"
        if status_code in {500, 502, 503, 504}:
            return f"provider_unavailable: upstream provider returned HTTP {status_code}"
        if isinstance(error, httpx.TimeoutException) or "timed out" in message.lower() or "timeout" in message.lower():
            return "provider_timeout: upstream provider request timed out"
        message = re.sub(r"https?://[^'\"\\s]+", "[REDACTED_URL]", message)
        return message[:500]

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
