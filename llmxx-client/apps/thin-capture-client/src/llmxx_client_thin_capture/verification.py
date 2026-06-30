# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/verification.py
# 產生時間: 2026-06-18 11:18 +08:00
# 版本: v0.3
# 模組定位:
#   thin capture client 的本機診斷與簡易比對工具。它協助工程人員確認 hotkey/capture/send
#   是否有跑到，但不能保存完整截圖或病歷影像。
# 主要責任:
#   1. 載入測試用 expected SOAP。
#   2. 提供舊測試流程使用的文字相似度比對。
#   3. 寫出遮蔽後 diagnostics JSON。
# 維護提醒:
#   - active thin client 不做 OCR，因此 verify_soap 只保留給相容測試，不代表正式閉環驗證。
#   - diagnostics 必須使用 payload.py 已遮蔽的 payload；不要在這裡加入 screenshot.image_b64。
# 驗證方式:
#   - run-once --no-send 產出的 diagnostics 不應含可還原截圖的 base64。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Optional

from .models import VerificationResult


def load_expected_soap(path: str | Path) -> Dict[str, str]:
    expected_path = Path(path)
    if not expected_path.exists():
        raise FileNotFoundError(f"Expected SOAP file not found: {expected_path}")
    with expected_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {field: str(data.get(field, "") or "") for field in ("S", "O", "A", "P")}


def verify_soap(actual: Dict[str, str], expected: Dict[str, str], threshold: float) -> VerificationResult:
    scores: Dict[str, float] = {}
    notes = []
    for field in ("S", "O", "A", "P"):
        # 這個比對只用於舊診斷流程；server OCR 搬移後，正式閉環以 server 回傳與醫師端浮窗為準。
        exp = _normalize(expected.get(field, ""))
        act = _normalize(actual.get(field, ""))
        score = SequenceMatcher(None, exp, act).ratio() if exp or act else 1.0
        scores[field] = round(score, 4)
        if score < threshold:
            notes.append(f"{field} mismatch: expected='{exp}' actual='{act}' score={score:.4f}")
    return VerificationResult(
        passed=all(score >= threshold for score in scores.values()),
        score_by_field=scores,
        expected=expected,
        actual=actual,
        notes=notes,
    )


def write_diagnostics(
    *,
    diagnostics_dir: str | Path,
    payload: Dict[str, Any],
    server_payload_path: str | None,
    diagnostics_payload: Dict[str, Any],
    verification: Optional[VerificationResult],
    server_response: Optional[Dict[str, Any]],
) -> Path:
    base = Path(diagnostics_dir)
    date_dir = base / datetime.now().strftime("%Y%m%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    out_path = date_dir / f"diagnostics_{datetime.now().strftime('%H%M%S_%f')}.json"

    # 診斷檔只能保存遮蔽後 JSON；payload.py 會把 screenshot.image_b64 改成遮蔽字串。
    # active client 不再產生 formal OCR payload，因此 server_payload_path 通常為 None。
    # 若未來要加入更多 debug 欄位，請先確認不含可還原影像或病患識別資訊。
    doc = {
        "server_payload_path": server_payload_path,
        "server_payload": payload,
        "diagnostics": diagnostics_payload,
        "verification": verification.to_dict() if verification else None,
        "server_response": server_response,
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return out_path


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().split()).lower()


