# 檔案路徑: rootmedicals-a/llmxx-server/server_app/core/response_builder.py
# 產生時間: 2026-06-18 09:30 +08:00
# 版本: v0.3-燈號原因優先序整理
# 模組定位:
#   這是 llmxx-server 的最後一道決策層。RAG/LAVA/fixture 已經回來後，
#   本檔把 ClinicalParse、EBM hits、adjudication 組成醫師端會看到的 response。
# 主要責任:
#   1. 檢查 evidence 是否可追溯到 paper_id/chunk_id 與 chunk metadata。
#   2. 執行 ICD 與 A/P 欄位的本機硬閘門，避免錯配案例被誤升成綠燈。
#   3. 決定 final_gate.light_color、display_mode、evidence_backed 與 reason_codes。
# 呼叫來源:
#   server_app.api.main.process_intake() 在 RAG 成功、fixture 成功或降級時呼叫。
# 輸入契約:
#   ClinicalParse 代表 server 已整理好的病歷；ebm_hits 必須盡量符合 RAG /check 回傳形狀。
# 輸出契約:
#   回傳前端 /api/client-sessions、/demo/latest、DoctorAlertWidget 共用的 JSON。
# 安全邊界:
#   本檔只允許「降級」，不允許因 demo 或 LLM 文字推論把缺來源、ICD 錯配、處置錯配升成綠燈。
#   黃燈表示需要人工確認；橘燈表示分類/診斷/處置有高風險錯配或 RAG hard gate。
# 維護提醒:
#   修改 reason code 時要同步更新 doctor_alert_widget.py 的臨床文字對照。
#   ICD/A 或 ICD/P 硬錯配是最優先原因；這種情境不要再混入缺文獻或 claim 分數不足，
#   否則醫師端會把真正該覆核的欄位看錯。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

from typing import Any

from ..infra.errors import get_error
from ..contracts.schemas import ClinicalParse
from ..ocr.server_ocr import expected_icd_label, icd_dx_mismatch_reason, normalize_icd_code


REQUIRED_SOURCE_METADATA = {"paper_id", "chunk_id"}
REQUIRED_CHUNK_METADATA = {"chunk_id", "paper_id", "six_s_level", "ocebm_level"}
ICD_TREATMENT_CONFLICT_TERMS = {
    "J30": {
        "oral anticoagulation",
        "anticoagulation",
        "anticoagulant",
        "doac",
        "warfarin",
        "apixaban",
        "rivaroxaban",
        "dabigatran",
        "edoxaban",
        "stroke prevention",
        "cha2ds2",
        "rate control",
        "rhythm control",
        "cardioversion",
        "atrial fibrillation",
        "afib",
    },
    "I48": {
        "intranasal corticosteroid",
        "nasal corticosteroid",
        "nasal steroid",
        "nasal spray",
        "fluticasone",
        "mometasone",
        "budesonide",
        "antihistamine",
        "cetirizine",
        "loratadine",
        "saline rinse",
        "hay fever",
        "rhinitis",
    },
}


def _comments_have_sources(ebm_hits: dict[str, Any]) -> bool:
    comments = ebm_hits.get("rag_comments")
    if not isinstance(comments, list):
        return False
    for comment in comments:
        if isinstance(comment, dict) and isinstance(comment.get("sources"), list) and comment["sources"]:
            return True
    return False


def _chunk_index(ebm_hits: dict[str, Any]) -> dict[str, dict[str, Any]]:
    retrieval = ebm_hits.get("retrieval") if isinstance(ebm_hits.get("retrieval"), dict) else {}
    chunks = retrieval.get("chunks") if isinstance(retrieval, dict) else []
    if not isinstance(chunks, list):
        return {}
    out = {}
    for chunk in chunks:
        if isinstance(chunk, dict) and chunk.get("chunk_id"):
            out[str(chunk["chunk_id"])] = chunk
    return out


def _source_validation_errors(ebm_hits: dict[str, Any]) -> list[str]:
    # Evidence-backed 顯示不是只看 LLM 摘要文字；至少要能追到來源 chunk。
    # 若來源 metadata 不完整，前端可以展示黃燈與原因，但不能標成 evidence_backed。
    errors: list[str] = []
    chunk_index = _chunk_index(ebm_hits)
    comments = ebm_hits.get("rag_comments")
    if not isinstance(comments, list) or not comments:
        return ["rag_comments_missing"]
    for comment in comments:
        if not isinstance(comment, dict):
            errors.append("rag_comment_invalid")
            continue
        sources = comment.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append("comment_sources_missing")
            continue
        for source in sources:
            if not isinstance(source, dict):
                errors.append("source_invalid")
                continue
            chunk_id = str(source.get("chunk_id") or "")
            chunk = chunk_index.get(chunk_id)
            if chunk:
                for key in ["paper_id", "pmid", "doi", "six_s_level", "ocebm_level"]:
                    if not source.get(key) and chunk.get(key):
                        source[key] = chunk.get(key)
            missing_source = [key for key in REQUIRED_SOURCE_METADATA if not source.get(key)]
            if missing_source:
                errors.append("source_metadata_missing")
            if not chunk:
                errors.append("source_chunk_missing")
                continue
            for key in REQUIRED_CHUNK_METADATA:
                if not chunk.get(key):
                    errors.append("chunk_metadata_missing")
            for key in ["pmid", "doi"]:
                if source.get(key) and chunk.get(key) and str(source.get(key)) != str(chunk.get(key)):
                    errors.append("source_metadata_mismatch")
    return sorted(set(errors))


def _demo_gate(ebm_hits: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    # 展示驗證分數是第二道保險：即使 fixture 產生 evidence，也必須有 pass verdict、
    # 足夠分數、且沒有 hard_fail_reasons，才允許進入綠燈候選。
    verifier = ebm_hits.get("demo_verifier")
    if not isinstance(verifier, dict):
        return False, {"verdict": "review", "score": 0, "hard_fail_reasons": ["demo_verifier_missing"]}
    verdict = str(verifier.get("verdict") or "review").lower()
    try:
        score = float(verifier.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    hard_fail_reasons = verifier.get("hard_fail_reasons")
    if not isinstance(hard_fail_reasons, list):
        hard_fail_reasons = []
    ok = verdict == "pass" and score >= 85 and not hard_fail_reasons
    return ok, {"verdict": verdict, "score": score, "hard_fail_reasons": hard_fail_reasons}


def _claim_gate(ebm_hits: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    # claim support 是「處置主張是否被 evidence 支持」的門檻。
    # 它和 ICD gate 不同：ICD gate 管疾病分類是否對，claim gate 管 P 欄處置是否有文獻支持。
    claim_verify = ebm_hits.get("claim_verify")
    if not isinstance(claim_verify, dict):
        demo_verifier = ebm_hits.get("demo_verifier")
        if isinstance(demo_verifier, dict) and isinstance(demo_verifier.get("claim_verification"), dict):
            claim_verify = demo_verifier.get("claim_verification")
    if not isinstance(claim_verify, dict):
        return False, {"overall_claim_support": 0.0, "verdict": "review"}
    try:
        support = float(claim_verify.get("overall_claim_support") or 0)
    except (TypeError, ValueError):
        support = 0.0
    verdict = "pass" if support >= 0.85 and int(claim_verify.get("contradiction_count") or 0) == 0 else "review"
    return verdict == "pass", {"overall_claim_support": support, "verdict": verdict}


def _contains_term(text: str, terms: set[str]) -> bool:
    safe_text = f" {str(text or '').lower()} "
    return any(str(term).lower() in safe_text for term in terms)


def _icd_tx_mismatch_reason(icd_code: str, tx_text: str) -> str:
    # 目前只放展示階段最容易誤解的疾病家族交叉用藥詞。
    # 正式版要改成 ICD taxonomy + drug/plan ontology，不要在這裡無限制堆字典。
    family = normalize_icd_code(icd_code)[:3]
    if not family or not str(tx_text or "").strip():
        return ""
    conflict_terms = ICD_TREATMENT_CONFLICT_TERMS.get(family, set())
    if _contains_term(tx_text, conflict_terms):
        return "icd_tx_mismatch"
    return ""


def _local_icd_gate_for_clinical(clinical: ClinicalParse, code: str, diagnosis_label: str) -> dict[str, Any]:
    if not code:
        return {"status": "review", "code": "", "family": "", "expected_label": "", "reason": "icd_missing"}

    expected_label = diagnosis_label or expected_icd_label(code)
    # 程式筆記：A 欄是醫師診斷心證，ICD 是疾病分類錨點；兩者若指向不同
    # 已知疾病家族，必須橘燈，不可因 RAG 缺資料而只停在黃燈。
    assessment_reason = icd_dx_mismatch_reason(code, clinical.dx_text or clinical.dx, diagnosis_label)
    if assessment_reason == "icd_dx_mismatch":
        return {"status": "fail", "code": code, "family": code[:3], "expected_label": expected_label, "reason": assessment_reason}

    # 程式筆記：P 欄是實際處置方向。若 ICD/A 是過敏性鼻炎，但處置寫成
    # AF 抗凝血；或 ICD/A 是 AF，但處置寫成鼻炎用藥，這是醫師端應立即
    # 看見的高風險錯配，因此直接橘燈。
    treatment_reason = _icd_tx_mismatch_reason(code, clinical.tx)
    if treatment_reason:
        return {"status": "fail", "code": code, "family": code[:3], "expected_label": expected_label, "reason": treatment_reason}

    return {"status": "pass", "code": code, "family": code[:3], "expected_label": expected_label, "reason": ""}


def _base_ebm(ebm_hits: dict[str, Any]) -> dict[str, Any]:
    return {
        "light_color": str(ebm_hits.get("light_color") or "yellow").lower(),
        "short_comment": str(ebm_hits.get("short_comment") or ebm_hits.get("summary") or ""),
        "rag_query_id": str(ebm_hits.get("query_id") or ""),
        "llmaaj_score": ebm_hits.get("llmaaj_score"),
        "rag_comments": ebm_hits.get("rag_comments") if isinstance(ebm_hits.get("rag_comments"), list) else [],
        "warnings": ebm_hits.get("warnings") if isinstance(ebm_hits.get("warnings"), list) else [],
        "icd_gate": ebm_hits.get("icd_gate") if isinstance(ebm_hits.get("icd_gate"), dict) else {},
        "retrieval": ebm_hits.get("retrieval") if isinstance(ebm_hits.get("retrieval"), dict) else {"phases": [], "chunks": []},
    }


def _icd_gate_for_response(clinical: ClinicalParse, ebm_hits: dict[str, Any] | None = None) -> dict[str, Any]:
    ebm_hits = ebm_hits or {}
    incoming = ebm_hits.get("icd_gate") if isinstance(ebm_hits.get("icd_gate"), dict) else {}
    code = normalize_icd_code(incoming.get("code") or clinical.icd10_code or clinical.icd_code)
    diagnosis_label = clinical.diagnosis_label or clinical.icd_label
    local_gate = _local_icd_gate_for_clinical(clinical, code, diagnosis_label)
    if incoming:
        gate = dict(incoming)
        gate["code"] = code
        gate.setdefault("expected_label", diagnosis_label or expected_icd_label(code))
        # 程式筆記：RAG 回來的 ICD gate 可以讓結果維持 review，但不能覆蓋
        # llmxx-server 從原始 HIS 欄位看到的明確錯配；本機硬閘門只做降級。
        if local_gate.get("status") == "fail":
            gate.update(local_gate)
            gate["local_hard_gate"] = True
        elif local_gate.get("status") == "review" and str(gate.get("status") or "").lower() == "pass":
            gate.update(local_gate)
        return gate
    return local_gate


def build_degraded_response(
    *,
    session_id: str,
    client_session_id: str,
    correlation_id: str,
    clinical: ClinicalParse,
    error_code: str,
    rag_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 降級回應用於 RAG timeout、payload 錯誤、provider 不可用等非完整成功路徑。
    # 這裡仍然會跑 ICD gate，因為即使 RAG 沒回來，ICD/A/P 明顯錯配也應該橘燈提醒。
    spec = get_error(error_code)
    ebm = _base_ebm(rag_payload or {})
    icd_gate = _icd_gate_for_response(clinical, rag_payload or {})
    light_color = "orange" if icd_gate.get("status") == "fail" else "yellow"
    ebm["light_color"] = light_color
    ebm["icd_gate"] = icd_gate
    reason_codes = [error_code]
    hard_fail_reasons = [error_code]
    if icd_gate.get("status") != "pass" and icd_gate.get("reason"):
        reason_codes.append(str(icd_gate.get("reason")))
        hard_fail_reasons.append(str(icd_gate.get("reason")))
    final_status = "not_evaluable" if spec.status == "not_evaluable" else "degraded"
    return {
        "ok": True,
        "status": final_status,
        "session_id": session_id,
        "client_session_id": client_session_id,
        "correlation_id": correlation_id,
        "error_code": error_code,
        "retryable": spec.retryable,
        "clinical_parse": clinical.model_dump(),
        "ebm": ebm,
        "adjudication": {},
        "claim_verify": {"overall_claim_support": 0.0, "verdict": "review"},
        "demo_verifier": {"verdict": "review", "score": 0, "hard_fail_reasons": [error_code]},
        "final_gate": {
            "light_color": light_color,
            "reason": spec.message,
            "reason_codes": sorted(set(reason_codes)),
            "hard_fail_reasons": sorted(set(hard_fail_reasons)),
            "icd_gate": icd_gate,
            "display_mode": "review",
            "evidence_backed": False,
        },
        "events_url": f"/api/sessions/{session_id}/events",
    }


def build_success_response(
    *,
    session_id: str,
    client_session_id: str,
    correlation_id: str,
    clinical: ClinicalParse,
    ebm_hits: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    # 成功回應不等於綠燈。這裡的「成功」只代表 server 拿到了可處理的 RAG/fixture 結果；
    # 是否 evidence_backed 要等來源、ICD、claim、verifier 全部過關。
    ebm = _base_ebm(ebm_hits)
    source_errors = _source_validation_errors(ebm_hits)
    has_sources = _comments_have_sources(ebm_hits)
    demo_ok, demo_verifier = _demo_gate(ebm_hits)
    claim_ok, claim_verify = _claim_gate(ebm_hits)
    icd_gate = _icd_gate_for_response(clinical, ebm_hits)
    light_color = ebm["light_color"] if ebm["light_color"] in {"green", "yellow", "orange"} else "yellow"
    icd_failed = icd_gate.get("status") == "fail"
    reason_codes: list[str] = []
    hard_fail_reasons: list[str] = []
    display_mode = "review"
    evidence_backed = False

    if light_color == "orange" and not icd_failed:
        reason_codes.append("rag_orange_hard_gate")
        hard_fail_reasons.append("rag_orange_hard_gate")
    if icd_failed:
        light_color = "orange"
        reason_codes.append(str(icd_gate.get("reason") or "icd_dx_mismatch"))
        hard_fail_reasons.append(str(icd_gate.get("reason") or "icd_dx_mismatch"))
    elif icd_gate.get("status") != "pass":
        reason = str(icd_gate.get("reason") or "icd_review_required")
        reason_codes.append(reason)
        hard_fail_reasons.append(reason)
        if light_color == "green":
            light_color = "yellow"
    if not icd_failed:
        if not has_sources:
            reason_codes.append("sources_missing")
            hard_fail_reasons.append("sources_missing")
        if source_errors:
            reason_codes.extend(source_errors)
            hard_fail_reasons.extend(source_errors)
        if not claim_ok:
            reason_codes.append("claim_support_below_evidence_backed_threshold")
        if not demo_ok:
            reason_codes.append("demo_verifier_not_pass")

    # 最後只在沒有 hard fail 且 claim/verifier 都通過時開 evidence_backed。
    # 這段是整套安全模型的核心：缺來源或 ICD 錯配永遠不能靠高分摘要變成綠燈。
    if hard_fail_reasons:
        status = "not_evaluable" if light_color != "orange" else "completed"
        if light_color == "green":
            light_color = "yellow"
    else:
        status = "completed"
        if claim_ok and demo_ok and light_color == "green":
            display_mode = "evidence_backed"
            evidence_backed = True

    final_gate = {
        "light_color": light_color,
        "reason": "Evidence-backed display allowed." if evidence_backed else "Review required before evidence-backed display.",
        "reason_codes": sorted(set(reason_codes)),
        "hard_fail_reasons": sorted(set(hard_fail_reasons)),
        "icd_gate": icd_gate,
        "display_mode": display_mode,
        "evidence_backed": evidence_backed,
    }
    ebm["light_color"] = light_color
    ebm["icd_gate"] = icd_gate
    return {
        "ok": True,
        "status": status,
        "session_id": session_id,
        "client_session_id": client_session_id,
        "correlation_id": correlation_id,
        "error_code": None,
        "retryable": False,
        "clinical_parse": clinical.model_dump(),
        "ebm": ebm,
        "adjudication": adjudication,
        "claim_verify": claim_verify,
        "demo_verifier": demo_verifier,
        "final_gate": final_gate,
        "events_url": f"/api/sessions/{session_id}/events",
    }

