# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/ebm_generate.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/ebm_generate.py
# Timestamp: 2026-06-16 16:55 +08:00
# Version: v0.4
# Description: LAVA matching task — EBM Generation。
#              讀取 ebm_generate binding，以 Top-K chunks 作為唯一證據來源呼叫 LLM。
#              binding 必須連到 verified active connection；支援從 LLM 文字中
#              擷取第一個 JSON object，避免 code fence/前後說明造成假失敗。
# Change Notes:
#              - v0.4: Add balanced JSON object extraction shared style with
#                optional LAVA verifier tasks. No evidence or scoring logic changed.
# ----------------------------------------------------------------------------------------------------

import json
import os
from lava.llm_model import LLMModel
from lava.adapter import get_adapter
from rag_core.core4_ragging.prompts import build_ebm_prompt

# 原本硬編 4096。實測在 Top-K=10（約 19K 字元證據）時對 gemini-2.5-flash 不夠：
# 該模型的 maxOutputTokens 涵蓋 thinking token，額度被推理吃光後 JSON 會從中間
# 截斷，finishReason 回 MAX_TOKENS。截斷的輸出又會被 _load_json_object 的
# raw_decode fallback 掃出「第一個完整的內層物件」（一則 rag_comments 項目），
# 於是回傳 status=ok 但形狀錯誤的 ebm_hits，最終在 pipeline 被判成
# ebm_hits_validation_failed → not_evaluable → 綠燈被保守降級成黃燈。
EBM_GENERATE_MAX_TOKENS = int(os.getenv("EBM_GENERATE_MAX_TOKENS", "16384"))

# 外層 envelope 的必要欄位；缺任何一個都代表拿到的不是完整結果。
_REQUIRED_ENVELOPE_KEYS = ("light_color", "short_comment", "rag_comments")


def _strip_json_fence(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text.startswith("```"):
        return text
    parts = text.split("```")
    if len(parts) >= 3:
        candidate = parts[1].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
        return candidate
    return text.lstrip("`").lstrip("json").strip()


def _load_json_object(raw_text: str) -> dict:
    text = _strip_json_fence(raw_text)
    decoder = json.JSONDecoder()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as first_error:
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[index:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise first_error
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("LLM EBM output must be a JSON object", text, 0)
    return parsed


async def execute_ebm_generate(payload: dict) -> dict:
    conn = LLMModel.get_connection_for_task("ebm_generate")
    if not conn:
        return {"status": "unconfigured", "error": "ebm_generate has no ready verified LAVA binding", "ebm_hits": None}

    conn = dict(conn)
    adapter = get_adapter(conn["provider"])
    if not adapter:
        return {"status": "failed", "error": f"Unknown provider: {conn['provider']}", "ebm_hits": None}

    chunks = payload.get("chunks", [])
    dx_summary = payload.get("dx_summary", "")
    case_context = payload.get("case_context", {})
    query_id = payload.get("query_id", "")

    messages = build_ebm_prompt(dx_summary, case_context, chunks)

    try:
        result = await adapter.chat(
            conn["api_key"], conn["model_id"], messages,
            temperature=0.0, max_tokens=EBM_GENERATE_MAX_TOKENS,
        )

        # adapter 有回傳 finish_reason，但舊版完全沒看，導致截斷靜默通過。
        finish_reason = str(result.get("finish_reason", "") or "").upper()
        if finish_reason == "MAX_TOKENS":
            return {
                "status": "failed",
                "error": (
                    "LLM output truncated (finishReason=MAX_TOKENS); "
                    f"raise EBM_GENERATE_MAX_TOKENS (current={EBM_GENERATE_MAX_TOKENS})"
                ),
                "ebm_hits": None,
            }

        ebm_hits = _load_json_object(result["content"])

        # _load_json_object 在外層 JSON 壞掉時會退而擷取內層物件，形狀可能不是
        # 完整 envelope。這裡明確擋掉，讓問題以「生成失敗」現形，而不是被下游
        # 報成一堆難以歸因的「欄位缺漏」。
        missing = [k for k in _REQUIRED_ENVELOPE_KEYS if k not in ebm_hits]
        if missing:
            return {
                "status": "failed",
                "error": (
                    "LLM returned JSON without the expected EBM envelope "
                    f"(missing: {', '.join(missing)}; finishReason={finish_reason}). "
                    "Likely a truncated or partially-extracted object."
                ),
                "ebm_hits": None,
            }

        ebm_hits["model"] = {"provider": conn["provider"], "model_id": conn["model_id"]}
        return {"status": "ok", "ebm_hits": ebm_hits, "error": None}
    except json.JSONDecodeError as e:
        return {"status": "failed", "error": f"LLM returned invalid JSON: {e}", "ebm_hits": None}
    except Exception as e:
        return {"status": "failed", "error": adapter.safe_error(e, conn.get("api_key", "")), "ebm_hits": None}
