# 模組定位: LAVA management/task API 的 Pydantic trust-boundary schemas。
# 主要責任: 驗證 connections、capability verify、binding 與 task invocation 請求。
# 呼叫來源: lava/api_router.py 與 API clients。
# 輸入契約: 未可信 JSON；provider/task/capability 與 image payload 受型別和欄位限制。
# 輸出契約: 僅產生 router/executor 可安全消費的 typed request models。
# 安全邊界: API key 只能進入 create/patch request，不得由 response model 原樣暴露。
# 維護提醒: optional Topic task 欄位不得降低既有 required readiness gate。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/schemas.py
# Timestamp: 2026-06-08
# Version: v0.2
# Description: LAVA API 的 Pydantic 請求/回應 Schema。
#              VerifyRequest 支援 capability，避免 chat 驗證結果被誤用於 embedding task。
# ----------------------------------------------------------------------------------------------------

from pydantic import BaseModel, field_validator
from typing import Optional

class ConnectionCreate(BaseModel):
    name: str = ""
    provider: str
    model_id: str = ""
    api_key: str
    rpm_limit: int = 60
    tpm_limit: int = 1000000

class ConnectionPatch(BaseModel):
    name: Optional[str] = None
    model_id: Optional[str] = None
    api_key: Optional[str] = None
    is_active: Optional[int] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None

class FetchModelsRequest(BaseModel):
    provider: str
    api_key: str

class VerifyRequest(BaseModel):
    connection_id: int
    capability: str = "chat"

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        capability = str(value or "").strip().lower()
        if capability not in {"chat", "embedding", "vision"}:
            raise ValueError("capability must be chat, embedding, or vision")
        return capability

class BindingUpdate(BaseModel):
    connection_id: int

class TaskInvokeRequest(BaseModel):
    connection_id: int
    payload: dict
