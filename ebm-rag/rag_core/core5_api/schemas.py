# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core5_api/schemas.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core5 API 層，對外提供 health/check/admin 等服務端路由。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core5_api/schemas.py
# Timestamp: 2026-06-17 00:00 +08:00
# Version: v0.5
# Description: Core5 API 請求 Schema。QueryRequest 供 /query 使用；CheckRequest 供 /check（llmxx 相容入口）使用。
# Change Notes:
#   - v0.2: Allow /check callers to pass safe retrieval filters, including the
#     explicit demo_synthetic_fallback verifier-gated option.
#   - v0.3: Accept ICD-10 code as a disease-classification anchor for the
#     conservative traffic-light gate.
#   - v0.4: Accept icd10_code, diagnosis_label, and dx_text so /check can
#     prioritize ICD-normalized diagnosis over OCR-only assessment text.
#   - v0.5: Accept normalized_diagnosis separately from display label.
# ----------------------------------------------------------------------------------------------------
from pydantic import BaseModel
from typing import Optional

class QueryRequest(BaseModel):
    dx_summary: str
    case_context: Optional[dict] = None
    filters: Optional[dict] = None
    top_k: int = 10

class CheckRequest(BaseModel):
    dx: str
    tx: Optional[str] = None
    hx: Optional[str] = None
    icd_code: Optional[str] = None
    icd10_code: Optional[str] = None
    diagnosis_label: Optional[str] = None
    normalized_diagnosis: Optional[str] = None
    dx_text: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    labs: Optional[dict] = None
    filters: Optional[dict] = None
    top_k: int = 10
