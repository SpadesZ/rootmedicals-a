# 檔案路徑: rootmedicals-a/llmxx-server/server_app/infra/security.py
# 產生時間: 2026-06-18 11:35 +08:00
# 版本: v0.2
# 模組定位:
#   llmxx-server intake 安全工具。這裡負責 payload shape 拒收、canonical hash、patient UID HMAC
#   與 AES-GCM envelope 解密。
# 主要責任:
#   1. 擋掉 diagnostics/debug wrapper 與不該進 formal intake 的影像欄位。
#   2. 產生穩定 payload hash，避免同一 client_session_id 對到不同 payload。
#   3. 將 patient UID 轉成不可逆參照值，不在 server 狀態表保存明文識別。
#   4. 支援 client 端可選 AES-GCM envelope。
# 維護提醒:
#   - thin screenshot payload 只允許走 ScreenshotClientPayload schema；formal JSON intake 不接受任意 image/debug wrapper。
#   - 安全錯誤要丟 SafeIntakeError，讓 API route 能回一致 error_code，而不是裸 500。
# 驗證方式:
#   - diagnostics wrapper / image_b64 應被拒收。
#   - AES key 缺失時應回 aes_key_unavailable，不得 silently downgrade。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from typing import Any

from .errors import get_error
from .settings import settings


FORBIDDEN_KEYS = {
    "diagnostics",
    "ocr",
    "raw_ocr_text",
    "screenshot",
    "screenshot_path",
    "image",
    "image_b64",
    "image_bytes",
    "server_payload_path",
    "capture",
    "window_capture",
}


class SafeIntakeError(ValueError):
    def __init__(self, error_code: str, detail: str | None = None):
        spec = get_error(error_code)
        super().__init__(detail or spec.message)
        self.error_code = error_code
        self.http_status = spec.http_status
        self.status = spec.status
        self.retryable = spec.retryable
        self.message = detail or spec.message


def _walk_forbidden_keys(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).strip().lower()
            if lowered in FORBIDDEN_KEYS:
                # formal intake 不接受 debug wrapper 或任意影像欄位；截圖必須走明確 schema，避免混入診斷檔。
                if lowered in {"screenshot", "screenshot_path", "image", "image_b64", "image_bytes", "window_capture"}:
                    return "image_payload_rejected"
                return "diagnostics_wrapper_rejected"
            found = _walk_forbidden_keys(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _walk_forbidden_keys(item, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str):
        compact = value.strip()
        if len(compact) > 1024 and re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", compact):
            # 大段 base64 形狀的字串很可能是影像或原始 payload，先拒收，避免 PHI 被塞進錯誤入口。
            return "image_payload_rejected"
    return None


def reject_forbidden_payload_shape(payload: Any) -> None:
    error_code = _walk_forbidden_keys(payload)
    if error_code:
        raise SafeIntakeError(error_code)


def canonical_json(value: Any) -> str:
    # sort_keys + compact separators 讓同一 payload 在不同 Python dict 順序下仍得到同一 hash。
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def patient_uid_ref(patient_uid: str) -> str | None:
    uid = str(patient_uid or "").strip()
    if not uid or uid == "[REDACTED]":
        return "[REDACTED]"
    key = os.environ.get(settings.patient_hmac_key_env_var, "")
    if not key:
        # 沒有 HMAC key 時寧可完全遮蔽，也不要把 patient_uid 明文落進狀態 DB。
        return "[REDACTED]"
    digest = hmac.new(key.encode("utf-8"), uid.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def decrypt_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    algorithm = str(payload.get("algorithm") or "").strip().upper()
    if algorithm != "AES-256-GCM":
        raise SafeIntakeError("aes_envelope_malformed")
    nonce_b64 = str(payload.get("nonce_b64") or "")
    ciphertext_b64 = str(payload.get("ciphertext_b64") or "")
    if not nonce_b64 or not ciphertext_b64:
        raise SafeIntakeError("aes_envelope_malformed")
    key_b64 = os.environ.get(settings.aes_key_env_var, "")
    if not key_b64:
        raise SafeIntakeError("aes_key_unavailable")
    try:
        key = base64.b64decode(key_b64)
        nonce = base64.b64decode(nonce_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
    except Exception as exc:
        raise SafeIntakeError("aes_envelope_malformed") from exc
    if len(key) != 32 or len(nonce) != 12:
        raise SafeIntakeError("aes_envelope_malformed")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:
        raise SafeIntakeError("aes_key_unavailable") from exc
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        decoded = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        # 解密失敗一律視為 envelope malformed，不回傳 cryptography 細節，避免洩漏部署狀態。
        raise SafeIntakeError("aes_envelope_malformed") from exc
    if not isinstance(decoded, dict):
        raise SafeIntakeError("aes_envelope_malformed")
    return decoded


def safe_json_loads(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SafeIntakeError("invalid_payload") from exc
    if not isinstance(parsed, dict):
        raise SafeIntakeError("invalid_payload")
    return parsed

