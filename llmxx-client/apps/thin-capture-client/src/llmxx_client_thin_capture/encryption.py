# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/encryption.py
# 產生時間: 2026-06-18 11:08 +08:00
# 版本: v0.2
# 模組定位:
#   thin capture client 的應用層 AES-GCM 封包工具。HTTPS 是傳輸層保護，這裡提供額外 envelope，
#   讓截圖 payload 在送到 llmxx-server 前可選擇加密。
# 主要責任:
#   1. 依 network.application_aes_gcm.enabled 判斷是否加密。
#   2. 從環境變數讀取 32-byte AES key。
#   3. 回傳 server 可辨識的 encrypted payload envelope。
# 維護提醒:
#   - 不要把 key 寫入 config 或 log；這裡只接受環境變數。
#   - nonce 必須每次送出重新產生，不可重用。
#   - 未啟用加密時直接回傳原 payload，讓本機 demo 保持最低摩擦。
# 驗證方式:
#   - 未啟用時 payload shape 不變。
#   - 啟用時回傳 schema_version / nonce_b64 / ciphertext_b64。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict


def maybe_encrypt_payload(payload: Dict[str, Any], network_config: Dict[str, Any]) -> Dict[str, Any]:
    aes_cfg = network_config.get("application_aes_gcm", {})
    if not aes_cfg.get("enabled", False):
        # 本機 demo 預設不加密，避免尚未設 key 時阻斷閉環；正式環境可由 config 啟用。
        return payload

    key_env = str(aes_cfg.get("key_env_var", "LLMXX_CLIENT_AES256_KEY_B64"))
    key_b64 = os.environ.get(key_env, "")
    if not key_b64:
        # 啟用加密卻沒有 key 是部署錯誤，必須硬失敗，不能偷偷降回明文。
        raise RuntimeError(f"AES-GCM is enabled but environment variable {key_env} is empty")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:
        raise RuntimeError("cryptography is required for AES-256-GCM encryption") from exc

    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("AES-256-GCM key must decode to exactly 32 bytes")

    # 96-bit nonce 是 AES-GCM 的常用長度；每次送出都重新產生，避免重用 nonce 破壞安全性。
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "schema_version": "llmxx-client-thin-capture.encrypted.v0.1",
        "algorithm": "AES-256-GCM",
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
    }



