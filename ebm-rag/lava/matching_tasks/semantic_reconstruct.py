# 檔案路徑: rootmedicals-a/ebm-rag/lava/matching_tasks/semantic_reconstruct.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/matching_tasks/semantic_reconstruct.py
# Timestamp: 2026-06-08
# Version: v0.3
# Description: LAVA matching task — Semantic Reconstruction。
#              讀取 semantic_reconstruct binding，呼叫 chat adapter 修補 OCR 文字。
#              binding 必須連到 verified active connection；無 ready binding → status="unconfigured"。
# ----------------------------------------------------------------------------------------------------

from lava.llm_model import LLMModel
from lava.adapter import get_adapter

async def execute_semantic_reconstruction(text_or_blocks) -> dict:
    conn = LLMModel.get_connection_for_task("semantic_reconstruct")
    if not conn:
        return {
            "status": "unconfigured",
            "reconstructed_text": text_or_blocks if isinstance(text_or_blocks, str) else str(text_or_blocks),
            "edits": [],
            "model": None,
            "connection_id": None,
            "error": "semantic_reconstruct has no ready verified LAVA binding"
        }

    conn = dict(conn)
    adapter = get_adapter(conn["provider"])
    if not adapter:
        return {"status": "failed", "reconstructed_text": str(text_or_blocks), "edits": [], "model": None, "connection_id": conn["id"], "error": f"Unknown provider: {conn['provider']}"}

    raw_text = text_or_blocks if isinstance(text_or_blocks, str) else " ".join(b.get("text", "") for b in text_or_blocks)

    system_msg = (
        "You are a medical text reconstructor. Fix OCR errors, rejoin broken words, "
        "preserve all clinical terminology exactly. Return ONLY the corrected text, no commentary."
    )
    messages = [
        {"role": "user", "content": f"[SYSTEM] {system_msg}\n\nOCR TEXT:\n{raw_text[:6000]}"}
    ]

    try:
        result = await adapter.chat(conn["api_key"], conn["model_id"], messages, temperature=0.0, max_tokens=4096)
        return {
            "status": "ok",
            "reconstructed_text": result["content"],
            "edits": [],
            "model": conn["model_id"],
            "connection_id": conn["id"],
            "error": None
        }
    except Exception as e:
        return {
            "status": "failed",
            "reconstructed_text": raw_text,
            "edits": [],
            "model": conn["model_id"],
            "connection_id": conn["id"],
            "error": adapter.safe_error(e, conn.get("api_key", ""))
        }
