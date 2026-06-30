# 檔案路徑: rootmedicals-a/llmxx-server/server_app/core/adjudicator.py
# 產生時間: 2026-06-18 11:25 +08:00
# 版本: v0.2
# 模組定位:
#   LLM-as-a-judge adjudication adapter。它嘗試呼叫 LAVA task `llmaaj_adjudicate`，失敗時回到
#   deterministic score，讓 final gate 可以安全降級。
# 主要責任:
#   1. 將 ClinicalParse 與 RAG hits 包成 LAVA payload。
#   2. 呼叫可選的 LLM adjudication task。
#   3. 在 LAVA 未綁定、timeout 或回傳不可用時產生保守 fallback score。
# 維護提醒:
#   - 這裡產生的是輔助分數，不是最終燈號。最終 green/yellow/orange 仍由 response_builder 的 gate 決定。
#   - 沒有來源或 verifier 未通過時，即使 LLM 分數高，也不能升級為 evidence-backed。
# 驗證方式:
#   - LAVA 未啟動時 /api/intake 仍應回應 yellow/review 或 orange/review，而不是 500。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from typing import Any

from ..integrations.lava_client import invoke_lava_task
from ..contracts.schemas import ClinicalParse


def deterministic_adjudication(clinical: ClinicalParse, ebm_hits: dict[str, Any]) -> dict[str, Any]:
    comments = ebm_hits.get("rag_comments") if isinstance(ebm_hits, dict) else []
    has_sources = False
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict) and isinstance(comment.get("sources"), list) and comment["sources"]:
                has_sources = True
                break
    light_color = str(ebm_hits.get("light_color") or "yellow").lower() if isinstance(ebm_hits, dict) else "yellow"
    base_support = {"green": 0.86, "yellow": 0.58, "orange": 0.2}.get(light_color, 0.0)
    if not has_sources:
        # 沒有可追溯來源時，分數必須壓低，讓 response_builder 維持 review，而不是假裝 evidence-backed。
        base_support = min(base_support, 0.25)
    return {
        "status": "fallback",
        "semantic_alignment_score": round(base_support, 2),
        "evidence_support_score": round(base_support, 2),
        "conflict_score": 0.8 if light_color == "orange" else 0.2,
        "risk_score": 0.9 if light_color == "orange" else 0.4,
        "reason": "Deterministic fallback adjudication; optional llmaaj_adjudicate was unavailable or not needed.",
    }


async def adjudicate(clinical: ClinicalParse, ebm_hits: dict[str, Any]) -> dict[str, Any]:
    # LAVA 是可選增強：有綁定就使用，沒有綁定就走 deterministic fallback，避免 demo 被 provider 狀態拖垮。
    payload = {
        "clinical_parse": clinical.model_dump(),
        "ebm_hits": ebm_hits if isinstance(ebm_hits, dict) else {},
    }
    result = await invoke_lava_task("llmaaj_adjudicate", payload)
    if result.ok and str(result.result.get("status") or "").lower() == "ok":
        return result.result
    fallback = deterministic_adjudication(clinical, ebm_hits)
    fallback["llm_status"] = result.error_code or "fallback"
    return fallback

