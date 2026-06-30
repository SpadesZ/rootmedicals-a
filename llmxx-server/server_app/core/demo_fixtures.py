# 檔案路徑: rootmedicals-a/llmxx-server/server_app/core/demo_fixtures.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.3-交付註解整理
# 模組定位:
#   這支檔案只處理「可重現展示資料」的 EBM 回覆，不是正式 live RAG。
#   它位在 /api/intake 收到 ClinicalParse 之後、response_builder 產生燈號之前。
# 主要責任:
#   1. 檢查 payload 是否明確要求 Demo Fixture，避免展示資料誤入一般臨床流程。
#   2. 對特定展示案例產生固定 evidence / rag_comments / verifier 結果，讓 demo 不受 provider 額度或逾時影響。
#   3. 讓回傳欄位形狀與 live RAG 相同，醫師浮窗與 /demo/latest 不需要分兩套路徑。
# 呼叫來源:
#   server_app.api.main.process_intake() 會在正式 RAG 呼叫前詢問本模組。
# 輸入契約:
#   FormalClientPayload 必須帶 demo_mode 與 demo_fixture_id；ClinicalParse 必須已完成 Dx/Tx/Hx 解析。
# 輸出契約:
#   回傳與 RAG /check 相容的 dict；不寫資料庫、不讀外部網路、不修改 payload。
# 安全邊界:
#   Demo Fixture 只能在 server 設定與 client marker 都明確啟用時使用。
#   任何不符合指定疾病與處置方向的內容都回傳 None，交回 live RAG 或安全降級流程。
# 維護提醒:
#   新增 fixture 時要同步補 claim_verify、demo_verifier、retrieval.chunks metadata，
#   並確認 response_builder 仍會因缺來源、ICD 錯配或 verifier 未過而降級。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import re
from typing import Any

from ..contracts.schemas import ClinicalParse, FormalClientPayload


ALLERGIC_RHINITIS_FIXTURE_ID = "allergic_rhinitis_intranasal_steroid"
# 對外 UI 與 payload mode 統一使用 demo fixture / deterministic demo 命名。

DEMO_MODE_ALIASES = {"demo_fixture", "deterministic_demo"}


def _normalized_contains(text: str, pattern: str) -> bool:
    # Fixture matching 只做非常保守的字串檢查；這裡不呼叫 LLM，避免展示路徑反而受 provider 狀態影響。
    compact = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return pattern in compact


def build_demo_ebm_fixture(payload: FormalClientPayload, clinical: ClinicalParse, *, enabled: bool) -> dict[str, Any] | None:
    # Demo fixture 是展示用保底路徑，不是正式文獻查詢。
    # enabled、demo_mode、fixture_id 三個條件缺一不可，避免 production live RAG 被假資料升級成綠燈。
    if not enabled:
        return None
    if str(payload.demo_mode or "").strip().lower() not in DEMO_MODE_ALIASES:
        return None
    if str(payload.demo_fixture_id or "").strip().lower() != ALLERGIC_RHINITIS_FIXTURE_ID:
        return None

    # 這裡只匹配一個很窄的過敏性鼻炎展示案例。
    # 若 A/P 與 allergic rhinitis 或鼻噴劑治療無關，fixture 直接放棄，交回 live RAG/安全 gate 處理。
    clinical_text = " ".join([clinical.dx, clinical.tx, clinical.hx]).lower()
    if not _normalized_contains(clinical_text, "allergic rhinitis"):
        return None
    if "spray" not in clinical_text and "intranasal" not in clinical_text and "nasal" not in clinical_text:
        return None

    paper_id = "RM_DEMO_AR_GUIDELINE_2026"
    chunk_id = "RM_DEMO_AR_GUIDELINE_2026_c001"
    doi = "10.0000/rootmedicals.demo.ar.2026"
    pmid = "DEMO-PMID-AR-001"
    comment = (
        "Demo Fixture：在沒有紅旗禁忌時，鼻用類固醇治療與過敏性鼻炎鼻部症狀控制一致。"
    )
    # chunk metadata 仍保持與 live RAG 相同欄位，讓 dashboard / doctor alert 可以用同一套 evidence renderer。
    chunk = {
        "chunk_id": chunk_id,
        "paper_id": paper_id,
        "source_type": "demo_fixture",
        "six_s_level": "guideline",
        "ocebm_level": "1a",
        "specialty": "allergy_ent",
        "disease": "allergic rhinitis",
        "guideline": True,
        "contraindication": False,
        "pmid": pmid,
        "doi": doi,
        "title": "RootMedicals 過敏性鼻炎可重現 Demo Fixture",
        "text": comment,
    }
    return {
        "status": "ok",
        "demo_only": True,
        "demo_fixture_id": ALLERGIC_RHINITIS_FIXTURE_ID,
        "query_id": "demo-allergic-rhinitis-001",
        "light_color": "green",
        "short_comment": comment,
        "summary": comment,
        "llmaaj_score": 0.91,
        # 這兩個 warning 是刻意留給 demo 報告看的稽核記號：來源是 fixture，不是 live provider。
        "warnings": [
            "demo_fixture_enabled",
            "demo_only_not_live_rag",
        ],
        "rag_comments": [
            {
                "claim": "鼻噴劑治療與過敏性鼻炎症狀控制一致。",
                "comment": comment,
                "sources": [
                    {
                        "paper_id": paper_id,
                        "chunk_id": chunk_id,
                        "pmid": pmid,
                        "doi": doi,
                    }
                ],
            }
        ],
        # retrieval.phases 模擬真正檢索紀錄，方便下一位工程師從 /demo/latest 追完整決策鏈。
        "retrieval": {
            "phases": [
                {
                    "phase": "demo_fixture",
                    "status": "matched",
                    "reason": "payload 明確啟用 Demo Fixture，且符合過敏性鼻炎 fixture marker。",
                    "top_k": 1,
                }
            ],
            "chunks": [chunk],
        },
        "claim_verify": {
            "overall_claim_support": 0.91,
            "contradiction_count": 0,
            "verdict": "pass",
        },
        "demo_verifier": {
            "verdict": "pass",
            "score": 92,
            "hard_fail_reasons": [],
            "fixture_id": ALLERGIC_RHINITIS_FIXTURE_ID,
        },
        "alternatives": [
            "正式上線前仍需使用 live RAG 來源完成 evidence-backed 驗證。",
            "若症狀或理學檢查提示感染，應維持 review 模式並由醫師確認。",
        ],
    }

