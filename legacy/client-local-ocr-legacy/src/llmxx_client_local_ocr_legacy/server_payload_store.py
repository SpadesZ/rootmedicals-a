# 檔案路徑: rootmedicals-a/legacy/client-local-ocr-legacy/src/llmxx_client_local_ocr_legacy/server_payload_store.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: LocalOCR 客戶端程式，負責截圖、OCR/薄客戶端傳送、醫師端提醒視窗與驗證工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# Path: ./llmxx-client-local-ocr/src/llmxx_client_local_ocr/server_payload_store.py
# Version History:
# v0.1 20260614-0000 - Persist formal server payload JSON separately from debug diagnostics.

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def write_server_payload(*, output_dir: str | Path, payload: Dict[str, Any]) -> Path:
    """Write the exact JSON body intended for llmxx-server intake."""
    base = Path(output_dir)
    date_dir = base / datetime.now().strftime("%Y%m%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    out_path = date_dir / f"server_payload_{datetime.now().strftime('%H%M%S_%f')}.json"

    # Inner v0.1: This file is the formal handoff artifact. It intentionally contains only
    # the payload that ServerClient.send() posts, while OCR raw text and coordinates remain
    # in diagnostics for local troubleshooting.
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path
