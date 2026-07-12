# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/doctor_alert_widget.py
# 產生時間: 2026-06-25 16:30 +08:00
# 版本: v0.4-臨床內容強化
# 模組定位:
#   醫師端 always-on-top 提醒浮窗。它不是工程 dashboard，而是 HIS 旁邊的臨床提示層。
# 主要責任:
#   1. 將 server response 的 final_gate/reason_codes 轉成醫師看得懂的中英文摘要。
#   2. 顯示綠/黃/橘/灰/紅燈、Dx、ICD、Tx、短評與可展開詳情。
#   3. 支援拖移、收合/展開、語言切換，不阻擋 HIS 繼續操作。
# 呼叫來源:
#   tray_app 在 Ctrl+Alt+G 後收到 response 或 polling 更新時呼叫本 widget。
# 輸入契約:
#   response dict 需至少包含 clinical_parse、ebm、final_gate；缺欄位時以安全預設顯示。
# 輸出契約:
#   只更新本機 UI，不寫檔、不回傳臨床決策。
# 安全邊界:
#   浮窗只呈現 server final_gate，不自行判斷是否 evidence-backed。
#   摘要的優先序必須讓 ICD/A/P 錯配高於文獻缺失，避免橘燈原因被黃燈文字稀釋。
# 維護提醒:
#   - 這個畫面預設是給醫師看的，不是工程 dashboard；Session、reason_code、score 等追蹤資訊
#     必須放在展開區尾端，且用「系統追蹤」命名，不要蓋過臨床覆核理由。
#   - 展開區前半段要回答醫師最自然的三個問題：診斷是否對、處置是否有依據、病人需要注意什麼。
#   - 新增 reason_code 時要同步 REASON_LABELS、TEXT 與 _clinical_comment() 的摘要優先序。
#   - 主題由 doctor_alert.theme 或 ROOTMEDICALS_ALERT_THEME 控制，控制台只傳設定，不改臨床判斷。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List


LIGHT_LABELS = {
    # 燈號文字維持短句，避免浮窗寬度被撐開；詳細原因放在展開區。
    "zh": {
        "green": ("GREEN", "綠燈", "#059669", "目前處置可參考"),
        "yellow": ("YELLOW", "黃燈", "#b77900", "需人工確認"),
        "orange": ("ORANGE", "橘燈", "#ea580c", "高風險，請先覆核"),
        "gray": ("PENDING", "處理中", "#64748b", "等待 EBM 結果"),
        "red": ("ERROR", "錯誤", "#dc2626", "請檢查流程"),
    },
    "en": {
        "green": ("GREEN", "Green", "#059669", "Plan may be referenced"),
        "yellow": ("YELLOW", "Yellow", "#b77900", "Physician review needed"),
        "orange": ("ORANGE", "Orange", "#ea580c", "High risk, review first"),
        "gray": ("PENDING", "Pending", "#64748b", "Waiting for EBM result"),
        "red": ("ERROR", "Error", "#dc2626", "Check workflow"),
    },
}

TEXT = {
    # 這裡放醫師會看到的語言，不放工程內部錯誤原文。
    # API 回傳的 reason_code 仍會在展開區保留，方便工程師對照 log。
    "zh": {
        "title": "RootMedicals EBM 提醒",
        "waiting": "等待 Ctrl+Alt+G",
        "dx": "診斷",
        "icd": "ICD",
        "tx": "處置",
        "click_expand": "點擊展開",
        "click_collapse": "點擊收合",
        "hide": "隱藏",
        "minimize": "最小化",
        "close": "關閉",
        "zoom_out": "縮小字",
        "zoom_in": "放大字",
        "language_button": "EN",
        "progress_default": "正在整理病歷並比對 EBM。",
        "busy": "上一筆仍在處理，請稍候。",
        "error_prefix": "流程錯誤",
        "review_details": "覆核資訊",
        "status": "狀態",
        "light": "燈號",
        "display": "顯示模式",
        "evidence_backed": "證據支持",
        "hx": "病史摘要",
        "reason_codes": "系統原因代碼",
        "hard_fails": "必要覆核原因",
        "scores": "分數",
        "evidence": "文獻來源",
        "warnings": "提醒",
        "alternatives": "可考慮方向",
        "reason": "原因",
        "clinical_summary": "臨床判讀摘要",
        "clinical_clues": "臨床重點",
        "diagnosis_review": "診斷與 ICD-10",
        "plan_fit": "處置合理性",
        "patient_counseling": "用藥與衛教提醒",
        "plan_review": "處置與 EBM 支持",
        "source_trace": "來源追溯",
        "review_required": "需要醫師覆核",
        "safety_review": "安全提醒",
        "system_tracking": "系統追蹤",
        "evidence_yes": "目前可追溯到文獻/指南來源。",
        "evidence_no": "目前尚未達到可直接採用的文獻支持門檻。",
        "green_action": "可作為處置參考；仍請依個案症狀、過敏史、禁忌症與院內規範調整。",
        "yellow_action": "請補齊 ICD-10 或確認文獻依據後再採用。",
        "orange_action": "請先修正診斷分類或處置方向，再重新送審。",
        "no_evidence": "目前沒有可顯示的文獻來源。",
        "none": "-",
        "green_comment": "目前處置與可用證據一致，未偵測到明顯紅旗或禁忌訊號。",
        "yellow_comment": "目前資訊不足或文獻支持尚未完整，建議醫師人工確認後再採用。",
        "orange_comment": "系統偵測到可能風險，請先覆核診斷、處置與禁忌症。",
        "red_comment": "EBM 流程未完成，請檢查系統或改由人工判讀。",
        "pending_comment": "正在擷取病歷並等待 EBM 回覆。",
        "rag_timeout": "EBM 查詢逾時，請稍後重試或人工確認。",
        "sources_missing": "目前缺少可追溯文獻來源，需人工確認。",
        "comments_missing": "目前缺少 EBM 摘要說明，需人工確認。",
        "provider_unavailable": "上游模型暫時不可用，請以人工判斷為準。",
        "server_response_timeout": "系統已送出，正在等待 EBM 回覆。",
        "invalid_payload": "未擷取到足夠病歷內容，請確認 HIS 畫面後重試。",
        "server_http_error": "伺服器回應異常，請確認流程後重試。",
        "icd_missing": "尚未讀到 ICD-10 分類，需人工確認。",
        "icd_dx_mismatch": "ICD-10 與診斷文字明顯不一致，請先覆核。",
        "icd_tx_mismatch": "ICD-10/診斷與處置方向明顯不一致，請先覆核。",
        "icd_dx_not_confirmed_by_assessment_text": "診斷文字未明確支持 ICD-10 分類，需人工確認。",
        "icd_unknown_family": "ICD-10 分類尚未在展示詞庫中確認，需人工確認。",
    },
    "en": {
        "title": "RootMedicals EBM Alert",
        "waiting": "Waiting for Ctrl+Alt+G",
        "dx": "Diagnosis",
        "icd": "ICD",
        "tx": "Plan",
        "click_expand": "Click to expand",
        "click_collapse": "Click to collapse",
        "hide": "Hide",
        "minimize": "Minimize",
        "close": "Close",
        "zoom_out": "Smaller",
        "zoom_in": "Larger",
        "language_button": "中文",
        "progress_default": "Reading the chart and checking EBM support.",
        "busy": "The previous review is still running.",
        "error_prefix": "Workflow error",
        "review_details": "Review Details",
        "status": "Status",
        "light": "Light",
        "display": "Display mode",
        "evidence_backed": "Evidence-backed",
        "hx": "History summary",
        "reason_codes": "System reason codes",
        "hard_fails": "Required review reasons",
        "scores": "Scores",
        "evidence": "Evidence sources",
        "warnings": "Warnings",
        "alternatives": "Possible alternatives",
        "reason": "Reason",
        "clinical_summary": "Clinical Summary",
        "clinical_clues": "Clinical Clues",
        "diagnosis_review": "Diagnosis and ICD-10",
        "plan_fit": "Plan Fit",
        "patient_counseling": "Medication and Counseling Notes",
        "plan_review": "Plan and EBM Support",
        "source_trace": "Source Trace",
        "review_required": "Physician Review Needed",
        "safety_review": "Safety Notes",
        "system_tracking": "System Tracking",
        "evidence_yes": "Traceable literature/guideline sources are available.",
        "evidence_no": "Evidence support has not reached the direct-use threshold.",
        "green_action": "May be used as a reference; still adjust for symptoms, allergy history, contraindications, and local policy.",
        "yellow_action": "Complete ICD-10 or confirm evidence support before use.",
        "orange_action": "Correct the diagnosis category or treatment direction, then review again.",
        "no_evidence": "No traceable evidence source is available yet.",
        "none": "-",
        "green_comment": "The current plan is consistent with available evidence; no obvious red flags or contraindication signals were detected.",
        "yellow_comment": "Evidence support is incomplete or the chart is insufficient; physician review is recommended before use.",
        "orange_comment": "A possible safety risk was detected; review the diagnosis, plan, and contraindications first.",
        "red_comment": "The EBM workflow did not complete; check the system or use manual review.",
        "pending_comment": "Capturing the chart and waiting for the EBM response.",
        "rag_timeout": "The EBM query timed out; retry later or review manually.",
        "sources_missing": "Traceable evidence sources are missing; manual review is needed.",
        "comments_missing": "The EBM summary is missing; manual review is needed.",
        "provider_unavailable": "The upstream model is temporarily unavailable; use clinical judgment.",
        "server_response_timeout": "The request was sent; waiting for the EBM response.",
        "invalid_payload": "Not enough chart text was captured; check the HIS screen and retry.",
        "server_http_error": "The server returned an error; check the workflow and retry.",
        "icd_missing": "ICD-10 classification was not captured; manual review is needed.",
        "icd_dx_mismatch": "ICD-10 and assessment text clearly conflict; review first.",
        "icd_tx_mismatch": "ICD-10/diagnosis and treatment plan clearly conflict; review first.",
        "icd_dx_not_confirmed_by_assessment_text": "Assessment text does not clearly confirm the ICD-10 anchor; review is needed.",
        "icd_unknown_family": "ICD-10 family is not confirmed in the demo dictionary; review is needed.",
    },
}

REASON_LABELS = {
    # reason_code 是 server contract；label 是醫師可讀文字。
    # 若只改 label 不改 code，不會破壞後端或既有測試。
    "zh": {
        "claim_support_below_evidence_backed_threshold": "文獻支持度未達可直接採用門檻",
        "demo_verifier_not_pass": "展示驗證未完全通過",
        "rag_comments_missing": "缺少 EBM 摘要說明",
        "sources_missing": "缺少可追溯文獻來源",
        "rag_timeout": "EBM 查詢逾時",
        "provider_unavailable": "上游模型暫時不可用",
        "server_response_timeout": "已送出，等待 EBM 回覆",
        "invalid_payload": "未擷取到足夠病歷內容",
        "server_http_error": "伺服器回應異常",
        "icd_missing": "尚未讀到 ICD-10 分類",
        "icd_dx_mismatch": "ICD-10 與診斷不一致",
        "icd_tx_mismatch": "ICD-10/診斷與處置方向不一致",
        "icd_dx_not_confirmed_by_assessment_text": "診斷文字未明確支持 ICD-10",
        "icd_unknown_family": "ICD-10 分類未確認",
    },
    "en": {
        "claim_support_below_evidence_backed_threshold": "Evidence support is below the direct-use threshold",
        "demo_verifier_not_pass": "Demo verifier did not fully pass",
        "rag_comments_missing": "EBM summary is missing",
        "sources_missing": "Traceable evidence sources are missing",
        "rag_timeout": "EBM query timed out",
        "provider_unavailable": "Upstream model is temporarily unavailable",
        "server_response_timeout": "Sent; waiting for EBM response",
        "invalid_payload": "Not enough chart text was captured",
        "server_http_error": "Server returned an error",
        "icd_missing": "ICD-10 classification was not captured",
        "icd_dx_mismatch": "ICD-10 conflicts with the diagnosis",
        "icd_tx_mismatch": "ICD-10/diagnosis conflicts with the plan",
        "icd_dx_not_confirmed_by_assessment_text": "Assessment does not clearly confirm ICD-10",
        "icd_unknown_family": "ICD-10 family is not confirmed",
    },
}

SCORE_LABELS = {
    "zh": {
        "semantic_alignment_score": "病歷與診斷處置一致度",
        "evidence_support_score": "文獻支持度",
        "conflict_score": "衝突訊號",
        "risk_score": "風險訊號",
        "claim_support": "主張支持判斷",
        "demo_verifier": "展示驗證",
    },
    "en": {
        "semantic_alignment_score": "Chart-plan alignment",
        "evidence_support_score": "Evidence support",
        "conflict_score": "Conflict signal",
        "risk_score": "Risk signal",
        "claim_support": "Claim support",
        "demo_verifier": "Demo verifier",
    },
}

THEMES = {
    # 主題只處理視覺，不改燈號與臨床文字。現場 demo 可以換底色，但不能因此改變 final_gate。
    "white": {
        "window": "rgba(255,255,255,246)",
        "border": "#cbd5e1",
        "text": "#0f172a",
        "muted": "#64748b",
        "button_bg": "#f8fafc",
        "detail_bg": "#f8fafc",
        "detail_text": "#0f172a",
        "detail_border": "#e2e8f0",
    },
    "microsoft_dark": {
        "window": "rgba(32,32,32,246)",
        "border": "#3b3b3b",
        "text": "#f3f3f3",
        "muted": "#b7b7b7",
        "button_bg": "#2d2d30",
        "detail_bg": "#1e1e1e",
        "detail_text": "#f3f3f3",
        "detail_border": "#505050",
    },
    "black": {
        "window": "rgba(0,0,0,246)",
        "border": "#334155",
        "text": "#f8fafc",
        "muted": "#cbd5e1",
        "button_bg": "#111827",
        "detail_bg": "#020617",
        "detail_text": "#e5e7eb",
        "detail_border": "#475569",
    },
}

THEME_ALIASES = {
    "": "white",
    "light": "white",
    "white": "white",
    "microsoft": "microsoft_dark",
    "microsoft_dark": "microsoft_dark",
    "ms_dark": "microsoft_dark",
    "dark": "black",
    "black": "black",
}


def _safe_str(value: Any, default: str = "-") -> str:
    text = str(value or "").strip()
    return text if text else default


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _format_list(values: Iterable[Any], empty_text: str = "-") -> str:
    items = [str(item).strip() for item in values if str(item or "").strip()]
    return "\n".join(f"- {item}" for item in items) if items else empty_text


def _format_reason_list(values: Iterable[Any], lang: str, empty_text: str = "-") -> str:
    labels = REASON_LABELS[lang]
    items: List[str] = []
    for value in values:
        code = str(value or "").strip()
        if not code:
            continue
        label = labels.get(code, "")
        items.append(f"- {label} ({code})" if label else f"- {code}")
    return "\n".join(items) if items else empty_text


def _format_clinical_reason_list(values: Iterable[Any], lang: str, empty_text: str = "-") -> str:
    labels = REASON_LABELS[lang]
    items: List[str] = []
    for value in values:
        code = str(value or "").strip()
        if not code:
            continue
        items.append(f"- {labels.get(code, code)}")
    return "\n".join(items) if items else empty_text


def _first_text(*values: Any, default: str = "-") -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return default


def _clip_text(value: Any, max_length: int = 86, default: str = "-") -> str:
    text = _safe_str(value, default)
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _bool_text(value: Any, lang: str) -> str:
    if isinstance(value, bool):
        if lang == "zh":
            return "是" if value else "否"
        return "Yes" if value else "No"
    return _safe_str(value)


def _clinical_action_text(light: str, lang: str) -> str:
    text = TEXT[lang]
    if light == "green":
        return text["green_action"]
    if light == "orange":
        return text["orange_action"]
    if light == "red":
        return text["red_comment"]
    if light == "gray":
        return text["pending_comment"]
    return text["yellow_action"]


def _resolve_theme(doctor_cfg: Dict[str, Any]) -> tuple[str, Dict[str, str]]:
    requested = _first_text(
        os.environ.get("ROOTMEDICALS_ALERT_THEME"),
        doctor_cfg.get("theme"),
        default="white",
    )
    key = THEME_ALIASES.get(str(requested or "").strip().lower(), "white")
    return key, THEMES[key]


def _theme_stylesheet(theme: Dict[str, str], scale: float = 1.0) -> str:
    # scale 讓醫師可在現場放大字級；只改字級與 padding，不動燈號顏色與臨床文字。
    base = max(9, round(12 * scale))
    btn = max(9, round(12 * scale))
    return f"""
        QWidget {{
            background: {theme["window"]};
            border: 1px solid {theme["border"]};
            border-radius: 8px;
            color: {theme["text"]};
            font-family: "Microsoft JhengHei UI", "Segoe UI", sans-serif;
            font-size: {base}px;
        }}
        QLabel {{ border: none; background: transparent; color: {theme["text"]}; }}
        QPushButton {{
            border: 1px solid {theme["border"]};
            border-radius: 4px;
            padding: 2px 6px;
            background: {theme["button_bg"]};
            color: {theme["text"]};
            font-size: {btn}px;
        }}
        QSizeGrip {{ background: transparent; border: none; width: 16px; height: 16px; }}
        QPlainTextEdit {{
            border: 1px solid {theme["detail_border"]};
            border-radius: 6px;
            background: {theme["detail_bg"]};
            color: {theme["detail_text"]};
            padding: 6px;
            font-family: "Microsoft JhengHei UI", "Segoe UI", sans-serif;
            font-size: {base}px;
        }}
        QScrollBar:vertical {{
            background: {theme["detail_bg"]};
            width: 12px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme["border"]};
            border-radius: 4px;
            min-height: 24px;
        }}
    """


def _reason_set(final_gate: Dict[str, Any]) -> set[str]:
    values = []
    values.extend(_as_list(final_gate.get("reason_codes")))
    values.extend(_as_list(final_gate.get("hard_fail_reasons")))
    icd_gate = _as_dict(final_gate.get("icd_gate"))
    values.append(icd_gate.get("reason"))
    return {str(item or "").strip().lower() for item in values if str(item or "").strip()}


def _icd_anchor_text(clinical: Dict[str, Any], final_gate: Dict[str, Any]) -> str:
    icd_gate = _as_dict(final_gate.get("icd_gate"))
    code = _first_text(icd_gate.get("code"), clinical.get("icd10_code"), clinical.get("icd_code"), default="")
    label = _first_text(icd_gate.get("expected_label"), clinical.get("diagnosis_label"), clinical.get("icd_label"), default="")
    if code and label:
        return f"{code} {label}"
    return code or label or "-"


def _clinical_comment(
    *,
    lang: str,
    light: str,
    final_gate: Dict[str, Any],
    ebm: Dict[str, Any],
    response: Dict[str, Any],
) -> str:
    text = TEXT[lang]
    reasons = _reason_set(final_gate)
    clinical = _as_dict(response.get("clinical_parse"))
    icd_anchor = _clip_text(_icd_anchor_text(clinical, final_gate), 72)
    dx_text = _clip_text(_first_text(clinical.get("dx_text"), clinical.get("dx"), default="-"), 72)
    tx_text = _clip_text(clinical.get("tx"), 92)
    raw_comment = _first_text(ebm.get("short_comment"), final_gate.get("reason"), response.get("error_code"), default="")
    raw_comment_lower = raw_comment.lower()

    # 程式筆記：摘要只放「醫師最需要先處理的主因」。ICD/A/P 這類硬錯配
    # 會造成橘燈，臨床風險高於文獻缺失，所以永遠排在 sources_missing 前面。
    if "icd_dx_mismatch" in reasons:
        if lang == "zh":
            return f"ICD 是 {icd_anchor}，但 A 欄是 {dx_text}，屬於 ICD-vs-A mismatch，請先覆核。"
        return f"ICD is {icd_anchor}, but the A field is {dx_text}; this is an ICD-vs-A mismatch. Review first."
    if "icd_tx_mismatch" in reasons:
        if lang == "zh":
            return f"ICD/A 是 {icd_anchor}，但處置欄是 {tx_text}，屬於 ICD-vs-P treatment mismatch，請先覆核。"
        return f"ICD/A is {icd_anchor}, but the plan is {tx_text}; this is an ICD-vs-P treatment mismatch. Review first."
    if "contraindication_not_respected" in reasons or "rag_orange_hard_gate" in reasons:
        return text["orange_comment"]
    if "timeout" in raw_comment_lower or "rag_timeout" in reasons:
        return text["rag_timeout"]
    if "provider_unavailable" in raw_comment_lower or "503" in raw_comment_lower:
        return text["provider_unavailable"]
    if "server_response_timeout" in reasons:
        return text["server_response_timeout"]
    if "invalid_payload" in reasons:
        return text["invalid_payload"]
    if "icd_missing" in reasons:
        return text["icd_missing"]
    if "icd_dx_not_confirmed_by_assessment_text" in reasons:
        return text["icd_dx_not_confirmed_by_assessment_text"]
    if "icd_unknown_family" in reasons:
        return text["icd_unknown_family"]
    if "claim_support_below_evidence_backed_threshold" in reasons:
        if lang == "zh":
            return "目前文獻支持度未達可直接採用門檻，需人工確認。"
        return "Evidence support is below the direct-use threshold; physician review is needed."
    if "sources_missing" in reasons:
        return text["sources_missing"]
    if "rag_comments_missing" in reasons:
        return text["comments_missing"]
    if light == "green":
        return text["green_comment"]
    if light == "orange":
        return text["orange_comment"]
    if light == "red":
        return text["red_comment"]
    if light == "gray":
        return text["pending_comment"]
    return text["yellow_comment"]


def _extract_evidence_lines(ebm: Dict[str, Any]) -> List[str]:
    # evidence 顯示優先用 rag_comments.sources；若來源缺欄位，再從 retrieval.chunks 補足。
    # 這樣醫師看到的是 paper/chunk/PMID/DOI，不是單純 LLM 摘要。
    lines: List[str] = []
    comments = _as_list(ebm.get("rag_comments"))
    retrieval = _as_dict(ebm.get("retrieval"))
    chunks = {
        str(chunk.get("chunk_id")): chunk
        for chunk in _as_list(retrieval.get("chunks"))
        if isinstance(chunk, dict) and chunk.get("chunk_id")
    }

    for index, comment in enumerate(comments, start=1):
        if not isinstance(comment, dict):
            continue
        comment_text = _safe_str(comment.get("comment") or comment.get("text") or comment.get("summary"), "")
        sources = _as_list(comment.get("sources"))
        if not sources and comment_text:
            lines.append(f"{index}. {comment_text}")
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            chunk_id = _safe_str(source.get("chunk_id"), "")
            chunk = chunks.get(chunk_id, {})
            paper_id = _first_text(source.get("paper_id"), chunk.get("paper_id"), default="-")
            pmid = _first_text(source.get("pmid"), chunk.get("pmid"), default="-")
            doi = _first_text(source.get("doi"), chunk.get("doi"), default="-")
            label = f"{index}. paper_id={paper_id}; chunk_id={chunk_id or '-'}; PMID={pmid}; DOI={doi}"
            if comment_text:
                label = f"{label}\n   {comment_text}"
            lines.append(label)

    if lines:
        return lines

    for chunk in chunks.values():
        paper_id = _safe_str(chunk.get("paper_id"))
        chunk_id = _safe_str(chunk.get("chunk_id"))
        pmid = _safe_str(chunk.get("pmid"))
        doi = _safe_str(chunk.get("doi"))
        lines.append(f"- paper_id={paper_id}; chunk_id={chunk_id}; PMID={pmid}; DOI={doi}")
    return lines


def _clean_doctor_text(value: Any) -> str:
    text = _safe_str(value, "")
    for prefix in ["Demo Fixture：", "Demo fixture:", "Demo Fixture:", "展示 fixture："]:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text


def _extract_evidence_summary_lines(ebm: Dict[str, Any], lang: str) -> List[str]:
    out: List[str] = []
    for comment in _as_list(ebm.get("rag_comments")):
        if not isinstance(comment, dict):
            continue
        claim = _clean_doctor_text(comment.get("claim"))
        comment_text = _clean_doctor_text(comment.get("comment") or comment.get("summary"))
        if claim:
            out.append(f"- {claim}")
        if comment_text and comment_text != claim:
            out.append(f"- {comment_text}")
    if out:
        return out
    if lang == "zh":
        return ["- 目前尚無足夠的臨床摘要可顯示。"]
    return ["- No concise clinical evidence summary is available yet."]


def _clinical_guidance_lines(clinical: Dict[str, Any], final_gate: Dict[str, Any], light: str, lang: str) -> Dict[str, List[str]]:
    dx = _first_text(clinical.get("dx_text"), clinical.get("dx"), default="").lower()
    tx = _safe_str(clinical.get("tx"), "").lower()
    hx = _safe_str(clinical.get("hx"), "").lower()
    reasons = _reason_set(final_gate)
    zh = lang == "zh"

    if "icd_dx_mismatch" in reasons:
        return {
            "clues": [
                "- ICD-10 與 A 欄診斷指向不同疾病分類。" if zh else "- ICD-10 and the assessment point to different disease categories.",
                "- 請先確認是否選錯 ICD、或 A 欄是否尚未更新。" if zh else "- Confirm whether the ICD was selected incorrectly or the assessment was not updated.",
            ],
            "fit": [
                "- 在 ICD 與診斷未一致前，系統不會放行綠燈。" if zh else "- Green light is blocked until ICD and diagnosis align.",
            ],
            "counsel": [
                "- 先修正診斷分類，再決定處置與衛教內容。" if zh else "- Correct the diagnostic category before using the plan for patient counseling.",
            ],
        }

    if "icd_missing" in reasons:
        return {
            "clues": [
                "- SOAP 內容可判讀，但尚未取得 ICD-10 作為疾病分類錨點。" if zh else "- SOAP content is interpretable, but ICD-10 is missing as the disease anchor.",
            ],
            "fit": [
                "- 即使 A/P 看起來合理，缺 ICD 時仍需人工確認後才能採用。" if zh else "- Even if A/P appears reasonable, missing ICD requires physician confirmation.",
            ],
            "counsel": [
                "- 補上 ICD-10 後再重新送出，可避免診斷分類與文獻查詢錯位。" if zh else "- Add ICD-10 and rerun to avoid mismatch between diagnosis category and evidence lookup.",
            ],
        }

    if "allergic rhinitis" in dx or "rhinitis" in dx or "nasal congestion" in hx:
        return {
            "clues": [
                "- 症狀以鼻塞、噴嚏或鼻黏膜腫脹為主，且目前未見發燒或呼吸困難等紅旗描述。" if zh else "- Symptoms center on nasal congestion/sneezing or mucosal swelling without fever or dyspnea red flags.",
                "- ICD-10 J30 系列與過敏性鼻炎分類相符。" if zh else "- ICD-10 J30 family aligns with allergic rhinitis.",
            ],
            "fit": [
                "- 鼻用類固醇常作為過敏性鼻炎鼻部症狀控制的一線選項之一。" if zh else "- Intranasal corticosteroid is a common first-line option for allergic rhinitis nasal symptom control.",
                "- 目前 P 欄處置與 A 欄診斷方向一致。" if zh else "- The plan is directionally consistent with the assessment.",
            ],
            "counsel": [
                "- 衛教可提醒規律使用、噴頭避開鼻中隔，並觀察鼻刺激或鼻出血。" if zh else "- Counsel regular use, aim away from the nasal septum, and watch for irritation or epistaxis.",
                "- 若症狀持續、單側鼻症狀、鼻出血或疑似感染，需回診重新評估。" if zh else "- Persistent symptoms, unilateral findings, epistaxis, or suspected infection should prompt reassessment.",
            ],
        }

    if "atrial fibrillation" in dx or "doac" in tx or "anticoagulation" in tx:
        return {
            "clues": [
                "- A 欄或處置內容指向心房顫動與中風預防評估。" if zh else "- Assessment/plan points to atrial fibrillation and stroke prevention review.",
                "- 抗凝處置前需確認 CHA2DS2-VASc、HAS-BLED、腎功能與出血風險。" if zh else "- Before anticoagulation, confirm CHA2DS2-VASc, HAS-BLED, renal function, and bleeding risk.",
            ],
            "fit": [
                "- 若 ICD 不是 I48 系列，應先修正診斷分類，不應直接放行綠燈。" if zh else "- If ICD is not I48 family, correct the diagnosis category before green-lighting.",
            ],
            "counsel": [
                "- 衛教需包含出血警訊、用藥交互作用與追蹤安排。" if zh else "- Counseling should include bleeding warning signs, drug interactions, and follow-up.",
            ],
        }

    return {
        "clues": ["- 請依 SOAP、ICD-10 與病人風險因子綜合判斷。" if zh else "- Interpret SOAP, ICD-10, and patient risk factors together."],
        "fit": ["- 系統僅提供 evidence-backed 參考，不取代醫師臨床判斷。" if zh else "- The system provides evidence-backed reference and does not replace clinical judgment."],
        "counsel": ["- 依個案禁忌症、過敏史與院內規範調整處置。" if zh else "- Adjust the plan for contraindications, allergies, and local policy."],
    }


def _format_doctor_warning_list(values: Iterable[Any], lang: str) -> str:
    hidden_demo_codes = {"demo_fixture_enabled", "demo_only_not_live_rag"}
    visible = [item for item in values if str(item or "").strip() not in hidden_demo_codes]
    if visible:
        return _format_list(visible)
    if lang == "zh":
        return "- 未偵測到需立即中止此處置的紅旗訊號；仍請依病人個別狀況判斷。"
    return "- No immediate stop signal was detected; still individualize to the patient."


class DoctorAlertWidget:
    def __init__(self, config: Dict[str, Any]):
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizeGrip, QVBoxLayout, QWidget
        except Exception as exc:
            raise RuntimeError("PySide6 is required for DoctorAlertWidget") from exc

        self.config = config
        self.expanded = False
        doctor_cfg = _as_dict(config.get("doctor_alert"))
        self.theme_key, self.theme = _resolve_theme(doctor_cfg)
        self.language = "en" if str(doctor_cfg.get("language") or "").strip().lower().startswith("en") else "zh"
        self._last_event: Dict[str, Any] = {"type": "progress", "message": TEXT[self.language]["waiting"]}
        self._drag_start_global = None
        self._drag_start_widget = None
        self._drag_moved = False
        self._user_positioned = False
        self._user_resized = False
        # 字級縮放：現場醫師常反映字太小，這裡讓 A-/A+ 動態放大，並記住上次設定。
        try:
            self.zoom = float(doctor_cfg.get("zoom", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.zoom = 1.0
        self.zoom = min(2.4, max(0.8, self.zoom))
        self._zoom_min = 0.8
        self._zoom_max = 2.4
        self._zoom_step_size = 0.15
        self.Qt = Qt
        self.widget = QWidget()
        self.widget.setWindowTitle("RootMedicals EBM Alert")
        # 用 Qt.Window（而非 Qt.Tool）讓最小化後在工作列有按鈕可以還原，
        # 不再像舊版「隱藏」後叫不回來。仍保持無邊框 + always-on-top。
        self.widget.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.widget.setStyleSheet(_theme_stylesheet(self.theme, self.zoom))

        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        self.title_label = QLabel(TEXT[self.language]["title"])
        self.title_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.status_label = QLabel(TEXT[self.language]["waiting"])
        self.status_label.setStyleSheet("font-weight: 700;")
        self.dx_label = QLabel(f"{TEXT[self.language]['dx']}: -")
        self.icd_label = QLabel(f"{TEXT[self.language]['icd']}: -")
        self.tx_label = QLabel(f"{TEXT[self.language]['tx']}: -")
        self.comment_label = QLabel("-")
        self.dx_label.setWordWrap(True)
        self.icd_label.setWordWrap(True)
        self.tx_label.setWordWrap(True)
        self.tx_label.setMaximumHeight(54)
        self.comment_label.setWordWrap(True)
        self.comment_label.setMaximumHeight(68)
        self.hint_label = QLabel(TEXT[self.language]["click_expand"])
        self.hint_label.setStyleSheet(f"color: {self.theme['muted']}; font-size: 10px;")
        self.detail_box = QPlainTextEdit()
        self.detail_box.setReadOnly(True)
        self.detail_box.setVisible(False)
        self.detail_box.setMinimumHeight(250)
        self.language_button = QPushButton(TEXT[self.language]["language_button"])
        self.language_button.setFixedWidth(54)
        self.language_button.clicked.connect(self._toggle_language)
        # 字體縮放按鈕：A－ / A＋。點按鈕不會觸發拖移或展開（子元件自行吃掉事件）。
        self.zoom_out_button = QPushButton("A－")
        self.zoom_out_button.setFixedWidth(40)
        self.zoom_out_button.setToolTip(TEXT[self.language]["zoom_out"])
        self.zoom_out_button.clicked.connect(lambda: self._zoom_step(-1))
        self.zoom_in_button = QPushButton("A＋")
        self.zoom_in_button.setFixedWidth(40)
        self.zoom_in_button.setToolTip(TEXT[self.language]["zoom_in"])
        self.zoom_in_button.clicked.connect(lambda: self._zoom_step(1))
        # 最小化：縮到工作列，之後可還原（取代舊的「隱藏」）。
        self.minimize_button = QPushButton(TEXT[self.language]["minimize"])
        self.minimize_button.setFixedWidth(64)
        self.minimize_button.clicked.connect(self._minimize)
        # 關閉：關掉這個提醒視窗；下一次 Ctrl+Alt+G 或 tray 還原會再叫回來。
        self.close_button = QPushButton(TEXT[self.language]["close"])
        self.close_button.setFixedWidth(56)
        self.close_button.clicked.connect(self.widget.close)
        # 右下角 size grip：讓無邊框視窗也能被使用者自由縮放。
        self.size_grip = QSizeGrip(self.widget)

        button_row = QHBoxLayout()
        button_row.addWidget(self.language_button, 0)
        button_row.addWidget(self.zoom_out_button, 0)
        button_row.addWidget(self.zoom_in_button, 0)
        button_row.addStretch(1)
        button_row.addWidget(self.minimize_button, 0)
        button_row.addWidget(self.close_button, 0)
        button_row.addWidget(self.size_grip, 0, self.Qt.AlignmentFlag.AlignBottom | self.Qt.AlignmentFlag.AlignRight)

        layout.addWidget(self.title_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.dx_label)
        layout.addWidget(self.icd_label)
        layout.addWidget(self.tx_label)
        layout.addWidget(self.comment_label)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.detail_box)
        layout.addLayout(button_row)

        self.widget.mousePressEvent = self._mouse_press_event
        self.widget.mouseMoveEvent = self._mouse_move_event
        self.widget.mouseReleaseEvent = self._mouse_release_event
        self._apply_zoom(initial=True)
        self._place_near_target_window()

    def show_event(self, event: Dict[str, Any]) -> None:
        self._last_event = dict(event)
        self.hint_label.setText(TEXT[self.language]["click_collapse"] if self.expanded else TEXT[self.language]["click_expand"])
        event_type = str(event.get("type") or "").strip().lower()
        if event_type in {"progress", "busy"}:
            self._show_progress(_safe_str(event.get("message"), TEXT[self.language]["progress_default"]), event_type)
            return
        if event_type == "hide":
            self.widget.hide()
            return
        if event_type == "error":
            self._show_error(_safe_str(event.get("message"), "Capture failed."))
            return
        if event_type in {"response", "poll"}:
            self._show_response(_as_dict(event.get("response")), event_type)
            return
        self._show_progress(TEXT[self.language]["pending_comment"], "progress")

    def _show_progress(self, message: str, event_type: str) -> None:
        text = TEXT[self.language]
        code, label, color, suffix = LIGHT_LABELS[self.language]["gray"]
        if event_type == "busy":
            message = text["busy"]
            suffix = text["busy"]
        self._set_status(code, label, color, suffix)
        self.dx_label.setText(f"{text['dx']}: -")
        self.icd_label.setText(f"{text['icd']}: -")
        self.tx_label.setText(f"{text['tx']}: -")
        self.comment_label.setText(message or text["pending_comment"])
        self.detail_box.setPlainText(message)
        self._show_widget()

    def _show_error(self, message: str) -> None:
        text = TEXT[self.language]
        code, label, color, suffix = LIGHT_LABELS[self.language]["red"]
        self._set_status(code, label, color, suffix)
        self.comment_label.setText(message)
        self.detail_box.setPlainText(f"{text['error_prefix']}\n{message}")
        self._show_widget()

    def _show_response(self, response: Dict[str, Any], event_type: str) -> None:
        clinical = _as_dict(response.get("clinical_parse"))
        ebm = _as_dict(response.get("ebm"))
        final_gate = _as_dict(response.get("final_gate"))
        adjudication = _as_dict(response.get("adjudication"))
        claim_verify = _as_dict(response.get("claim_verify"))
        demo_verifier = _as_dict(response.get("demo_verifier"))

        light = _first_text(final_gate.get("light_color"), ebm.get("light_color"), default="yellow").lower()
        if light not in {"green", "yellow", "orange", "gray", "red"}:
            light = "yellow"
        text = TEXT[self.language]
        code, label, color, suffix = LIGHT_LABELS[self.language][light]
        display_mode = _safe_str(final_gate.get("display_mode"), "review")
        if event_type == "poll":
            suffix = f"{suffix} / update" if self.language == "en" else f"{suffix} / 更新"
        self._set_status(code, label, color, suffix)

        dx = _safe_str(clinical.get("dx"))
        icd = _safe_str(clinical.get("icd_code"))
        tx = _safe_str(clinical.get("tx"))
        hx = _safe_str(clinical.get("hx"))
        short_comment = _clinical_comment(
            lang=self.language,
            light=light,
            final_gate=final_gate,
            ebm=ebm,
            response=response,
        )
        self.dx_label.setText(f"{text['dx']}: {dx}")
        self.icd_label.setText(f"{text['icd']}: {icd}")
        self.tx_label.setText(f"{text['tx']}: {tx}")
        self.comment_label.setText(short_comment)

        evidence_lines = _extract_evidence_lines(ebm)
        evidence_summary_lines = _extract_evidence_summary_lines(ebm, self.language)
        clinical_guidance = _clinical_guidance_lines(clinical, final_gate, light, self.language)
        reason_codes = _as_list(final_gate.get("reason_codes"))
        hard_fail_reasons = _as_list(final_gate.get("hard_fail_reasons"))
        icd_gate = _as_dict(final_gate.get("icd_gate") or ebm.get("icd_gate"))
        warnings = _as_list(ebm.get("warnings"))
        alternatives = _as_list(ebm.get("alternatives") or response.get("alternatives"))
        score_labels = SCORE_LABELS[self.language]
        score_lines = [
            f"{score_labels['semantic_alignment_score']}: {_safe_str(adjudication.get('semantic_alignment_score'))}",
            f"{score_labels['evidence_support_score']}: {_safe_str(adjudication.get('evidence_support_score'))}",
            f"{score_labels['conflict_score']}: {_safe_str(adjudication.get('conflict_score'))}",
            f"{score_labels['risk_score']}: {_safe_str(adjudication.get('risk_score'))}",
            f"{score_labels['claim_support']}: {_safe_str(claim_verify.get('overall_claim_support'))}",
            f"{score_labels['demo_verifier']}: {_safe_str(demo_verifier.get('verdict'))} / {_safe_str(demo_verifier.get('score'))}",
        ]
        evidence_backed = bool(final_gate.get("evidence_backed"))
        evidence_summary = text["evidence_yes"] if evidence_backed else text["evidence_no"]
        required_review = _format_clinical_reason_list(hard_fail_reasons or reason_codes, self.language)
        action_text = _clinical_action_text(light, self.language)
        # 程式筆記:
        # 展開區前半段是醫師判讀路徑，後半段才是系統追蹤。這個順序很重要：
        # 現場 demo 時醫師會先問「我為什麼要改病歷或處置」，不是先問 session_id。
        detail = [
            f"【{text['clinical_summary']}】",
            f"{text['light']}: {label}",
            f"{text['reason']}: {short_comment}",
            f"建議: {action_text}" if self.language == "zh" else f"Action: {action_text}",
            f"{text['evidence_backed']}: {_bool_text(evidence_backed, self.language)}",
            "",
            f"【{text['clinical_clues']}】",
            "\n".join(clinical_guidance["clues"]),
            "",
            f"【{text['diagnosis_review']}】",
            f"{text['dx']}: {dx}",
            f"{text['icd']}: {icd}",
            f"{text['reason']}: {_safe_str(icd_gate.get('reason'))}",
            f"{text['hx']}: {hx}",
            "",
            f"【{text['plan_fit']}】",
            "\n".join(clinical_guidance["fit"]),
            "",
            f"【{text['patient_counseling']}】",
            "\n".join(clinical_guidance["counsel"]),
            "",
            f"【{text['plan_review']}】",
            f"{text['tx']}: {tx}",
            evidence_summary,
            "\n".join(evidence_summary_lines),
            "",
            f"【{text['source_trace']}】",
            "\n".join(evidence_lines) if evidence_lines else text["no_evidence"],
            "",
            f"【{text['review_required']}】",
            required_review,
            "",
            f"【{text['safety_review']}】",
            _format_doctor_warning_list(warnings, self.language),
            "",
            f"【{text['alternatives']}】",
            _format_list(alternatives),
            "",
            f"【{text['system_tracking']}】",
            f"Session: {_safe_str(response.get('session_id'))}",
            f"{text['status']}: {_safe_str(response.get('status'))}",
            f"{text['display']}: {display_mode}",
            "",
            f"{text['scores']}:",
            "\n".join(score_lines),
            "",
            f"{text['reason_codes']}:",
            _format_reason_list(reason_codes, self.language),
            "",
            f"{text['hard_fails']}:",
            _format_reason_list(hard_fail_reasons, self.language),
            "",
            f"{text['reason']}: {_safe_str(final_gate.get('reason'))}",
        ]
        self.detail_box.setPlainText("\n".join(detail))
        self._show_widget()

    def _set_status(self, code: str, label: str, color: str, suffix: str) -> None:
        self.status_label.setText(f"● {label}  {suffix}")
        self.status_label.setStyleSheet(f"font-weight: 700; color: {color};")
        self.title_label.setText(f"{TEXT[self.language]['title']} · {code}")

    def _toggle_language(self) -> None:
        self.language = "en" if self.language == "zh" else "zh"
        self.language_button.setText(TEXT[self.language]["language_button"])
        self.minimize_button.setText(TEXT[self.language]["minimize"])
        self.close_button.setText(TEXT[self.language]["close"])
        self.zoom_out_button.setToolTip(TEXT[self.language]["zoom_out"])
        self.zoom_in_button.setToolTip(TEXT[self.language]["zoom_in"])
        self.show_event(self._last_event)

    def _mouse_press_event(self, event: Any) -> None:
        if event.button() != self.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._drag_start_global = self._event_global_pos(event)
        self._drag_start_widget = self.widget.pos()
        self._drag_moved = False
        event.accept()

    def _mouse_move_event(self, event: Any) -> None:
        if not (event.buttons() & self.Qt.MouseButton.LeftButton):
            event.ignore()
            return
        if self._drag_start_global is None or self._drag_start_widget is None:
            event.ignore()
            return
        delta = self._event_global_pos(event) - self._drag_start_global
        if delta.manhattanLength() >= 4:
            self._drag_moved = True
        if self._drag_moved:
            self.widget.move(self._drag_start_widget + delta)
        event.accept()

    def _mouse_release_event(self, event: Any) -> None:
        if event.button() != self.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if not self._drag_moved:
            self._toggle_expanded()
        else:
            self._user_positioned = True
        self._drag_start_global = None
        self._drag_start_widget = None
        self._drag_moved = False
        event.accept()

    def _toggle_expanded(self) -> None:
        self.expanded = not self.expanded
        self.detail_box.setVisible(self.expanded)
        self.hint_label.setText(TEXT[self.language]["click_collapse"] if self.expanded else TEXT[self.language]["click_expand"])
        self._resize()
        self.widget.repaint()

    def _event_global_pos(self, event: Any) -> Any:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _resize(self) -> None:
        # 不再用 setFixedSize，改成 min size + resize，這樣 QSizeGrip 才能讓使用者自由縮放。
        z = self.zoom
        width = round((360 if not self.expanded else 460) * z)
        height = round((245 if not self.expanded else 560) * z)
        self.widget.setMinimumSize(round(280 * z), round(170 * z))
        self.widget.resize(width, height)

    def _apply_zoom(self, initial: bool = False) -> None:
        # 依 self.zoom 重算字級與關鍵高度，讓現場字太小時可放大看清楚。
        z = self.zoom
        self.widget.setStyleSheet(_theme_stylesheet(self.theme, z))
        self.title_label.setStyleSheet(f"font-weight: 700; font-size: {round(12 * z)}px;")
        self.hint_label.setStyleSheet(f"color: {self.theme['muted']}; font-size: {round(10 * z)}px;")
        self.tx_label.setMaximumHeight(round(54 * z))
        self.comment_label.setMaximumHeight(round(68 * z))
        self.detail_box.setMinimumHeight(round(250 * z))
        self._resize()
        if not initial:
            # 狀態列的顏色/字樣是動態設定的，重畫一次確保縮放後樣式正確。
            self.show_event(self._last_event)

    def _zoom_step(self, direction: int) -> None:
        new_zoom = round(self.zoom + direction * self._zoom_step_size, 2)
        new_zoom = min(self._zoom_max, max(self._zoom_min, new_zoom))
        if abs(new_zoom - self.zoom) < 1e-6:
            return
        self.zoom = new_zoom
        self._apply_zoom()

    def _minimize(self) -> None:
        # 最小化到工作列，之後可從工作列或 tray 選單還原（取代舊的「隱藏」）。
        self.widget.showMinimized()

    def restore(self) -> None:
        # 供 tray 選單「顯示/還原 EBM 視窗」呼叫：不論最小化或已關閉都能叫回來。
        if not self._user_positioned:
            self._place_near_target_window()
        self.widget.showNormal()
        self.widget.raise_()
        self.widget.activateWindow()

    def _show_widget(self) -> None:
        if not self._user_positioned:
            self._place_near_target_window()
        if self.widget.isMinimized():
            self.widget.showNormal()
        else:
            self.widget.show()
        self.widget.raise_()

    def _place_near_target_window(self) -> None:
        try:
            left, top, right, bottom = self._find_target_window_rect()
            if right > left and bottom > top:
                self._move_within_screen(right + 12, top + 64)
                return
        except Exception:
            pass
        self._move_to_screen_corner()

    def _find_target_window_rect(self) -> tuple[int, int, int, int]:
        if os.name != "nt":
            return (0, 0, 0, 0)
        try:
            import win32gui
        except Exception:
            return (0, 0, 0, 0)

        title_parts = [
            str(item).lower()
            for item in self.config.get("target_window", {}).get("title_contains", [])
            if str(item).strip()
        ]
        if not title_parts:
            title_parts = ["clinical guard"]
        matches: List[tuple[int, int, int, int]] = []

        def enum_handler(hwnd: int, _: Any) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").lower()
            if any(part in title for part in title_parts):
                rect = win32gui.GetWindowRect(hwnd)
                matches.append(tuple(int(v) for v in rect))

        win32gui.EnumWindows(enum_handler, None)
        return matches[0] if matches else (0, 0, 0, 0)

    def _move_within_screen(self, x: int, y: int) -> None:
        screen = self.widget.screen()
        geometry = screen.availableGeometry() if screen else None
        if not geometry:
            self.widget.move(max(20, x), max(20, y))
            return
        max_x = geometry.right() - self.widget.width() - 8
        max_y = geometry.bottom() - self.widget.height() - 8
        move_x = min(max(geometry.left() + 8, x), max_x)
        move_y = min(max(geometry.top() + 8, y), max_y)
        self.widget.move(move_x, move_y)

    def _move_to_screen_corner(self) -> None:
        screen = self.widget.screen()
        geometry = screen.availableGeometry() if screen else None
        if not geometry:
            self.widget.move(40, 40)
            return
        self.widget.move(geometry.right() - self.widget.width() - 16, geometry.top() + 88)


