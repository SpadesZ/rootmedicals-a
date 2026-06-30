# 檔案路徑: rootmedicals-a/ebm-rag/lava/schemas.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
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
        if capability not in {"chat", "embedding"}:
            raise ValueError("capability must be chat or embedding")
        return capability

class BindingUpdate(BaseModel):
    connection_id: int

class TaskInvokeRequest(BaseModel):
    connection_id: int
    payload: dict
