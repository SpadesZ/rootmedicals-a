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
AF_ACTIVE_BLEEDING_FIXTURE_ID = "atrial_fibrillation_active_bleeding"
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
    fixture_id = str(payload.demo_fixture_id or "").strip().lower()
    clinical_text = " ".join([clinical.dx, clinical.tx, clinical.hx]).lower()
    if fixture_id == ALLERGIC_RHINITIS_FIXTURE_ID:
        if not _normalized_contains(clinical_text, "allergic rhinitis"):
            return None
        if "spray" not in clinical_text and "intranasal" not in clinical_text and "nasal" not in clinical_text:
            return None
        paper_id = "PMID_32707227"
        chunk_id = "PMID_32707227_c001"
        title = "Rhinitis 2020: A practice parameter update"
        journal = "Journal of Allergy and Clinical Immunology"
        publication_year = 2020
        volume = "146"
        issue = "4"
        pages = "721-767"
        doi = "10.1016/j.jaci.2020.07.007"
        pmid = "32707227"
        light_color = "green"
        specialty = "allergy_ent"
        disease = "allergic rhinitis"
        claim = "鼻用類固醇治療與過敏性鼻炎症狀控制一致。"
        comment = "過敏性鼻炎診療參數指出，鼻用類固醇仍是持續性過敏性鼻炎的首選單方治療。"
        has_contraindication = False
        warnings: list[Any] = ["demo_fixture_enabled", "demo_only_not_live_rag"]
        alternatives = [
            "確認鼻噴劑使用方式、規律性與療效。",
            "症狀持續或出現單側鼻塞、鼻出血、發燒時重新評估。",
        ]
    elif fixture_id == AF_ACTIVE_BLEEDING_FIXTURE_ID:
        if not _normalized_contains(clinical_text, "atrial fibrillation"):
            return None
        if not any(term in clinical_text for term in ("apixaban", "anticoagulation", "anticoagulant")):
            return None
        if not any(term in clinical_text for term in ("active bleeding", "active gastrointestinal bleeding", "major bleeding")):
            return None
        paper_id = "PMID_38033089"
        chunk_id = "PMID_38033089_c001"
        title = "2023 ACC/AHA/ACCP/HRS Guideline for the Diagnosis and Management of Atrial Fibrillation"
        journal = "Circulation"
        publication_year = 2024
        volume = "149"
        issue = "1"
        pages = "e1-e156"
        doi = "10.1161/CIR.0000000000001193"
        pmid = "38033089"
        light_color = "orange"
        specialty = "cardiology"
        disease = "atrial fibrillation"
        claim = "活動性重大出血期間不應直接立即啟動抗凝血，需先處理出血並重新評估。"
        comment = "心房顫動指引包含抗凝治療中活動性出血的處置；目前病例有活動性腸胃道出血，立即啟動 apixaban 存在重大安全衝突。"
        has_contraindication = True
        warnings = [
            "demo_fixture_enabled",
            "demo_only_not_live_rag",
            {
                "code": "contraindication_hard_gate",
                "type": "contraindication",
                "reason": "patient_context_contains_hard_contraindication",
                "message": "Active gastrointestinal bleeding conflicts with immediate anticoagulation initiation.",
            },
        ]
        alternatives = [
            "暫緩立即啟動抗凝血並先評估出血嚴重度與來源。",
            "出血控制後，再依中風與出血風險評估抗凝血恢復時機。",
        ]
    else:
        return None

    # chunk metadata 仍保持與 live RAG 相同欄位，讓 dashboard / doctor alert 可以用同一套 evidence renderer。
    chunk = {
        "chunk_id": chunk_id,
        "paper_id": paper_id,
        "source_type": "guideline",
        "six_s_level": "guideline",
        "ocebm_level": "1a",
        "specialty": specialty,
        "disease": disease,
        "guideline": True,
        "is_guideline": True,
        "contraindication": has_contraindication,
        "has_contraindication_terms": has_contraindication,
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "guideline_title": title,
        "journal": journal,
        "publication_year": publication_year,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "text": comment,
    }
    return {
        "status": "ok",
        "demo_only": True,
        "demo_fixture_id": fixture_id,
        "query_id": f"demo-{fixture_id}-001",
        "light_color": light_color,
        "short_comment": comment,
        "summary": comment,
        "llmaaj_score": 0.94,
        # 這兩個 warning 是刻意留給 demo 報告看的稽核記號：來源是 fixture，不是 live provider。
        "warnings": warnings,
        "rag_comments": [
            {
                "claim": claim,
                "comment": comment,
                "sources": [
                    {
                        "paper_id": paper_id,
                        "chunk_id": chunk_id,
                        "pmid": pmid,
                        "doi": doi,
                        "title": title,
                        "journal": journal,
                        "publication_year": publication_year,
                        "volume": volume,
                        "issue": issue,
                        "pages": pages,
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
                    "reason": f"payload 明確啟用 Demo Fixture，且符合 {fixture_id} marker。",
                    "top_k": 1,
                }
            ],
            "chunks": [chunk],
        },
        "claim_verify": {
            "overall_claim_support": 0.94,
            "contradiction_count": 0,
            "verdict": "pass",
        },
        "demo_verifier": {
            "verdict": "pass",
            "score": 96,
            "hard_fail_reasons": [],
            "fixture_id": fixture_id,
        },
        "alternatives": alternatives,
    }

