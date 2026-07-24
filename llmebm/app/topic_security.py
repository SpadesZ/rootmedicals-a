# 模組定位: llmebm Topic content 生成端點的最小授權邊界。
# 主要責任: 保護生成端點、限制 reviewer UI 單-slot工作，並管理 dev-only review session 啟用條件。
# 呼叫來源: main_ebm.py 的 Topic content generate route 與 contract tests。
# 輸入契約: client host、request token、slot/status 與可選 configured token，皆視為不可信輸入。
# 輸出契約: 僅回傳布林授權結果，不回傳或記錄 secret。
# 安全邊界: 遠端請求不可利用空 token；dev session 只允許明確 local/dev 環境且仍須 configured token。
# 維護提醒: 若改成多 instance/公開服務，應改用正式 identity gateway，不在此擴充 ACL。
# ----------------------------------------------------------------------------------------------------

import hmac
import ipaddress
import os
import threading
import time


_RATE_LOCK = threading.Lock()
_MUTATION_WINDOWS = {}


def generation_request_authorized(client_host, provided_token, configured_token=None):
    configured = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "") if configured_token is None else configured_token
    if configured:
        return bool(provided_token) and hmac.compare_digest(provided_token, configured)
    try:
        return ipaddress.ip_address(client_host or "").is_loopback
    except ValueError:
        return False


def review_request_authorized(provided_token, configured_token=None):
    """Medical workflow mutations never inherit the loopback-only development exception."""
    configured = os.getenv("LLMEBM_REVIEW_ADMIN_TOKEN", "") if configured_token is None else configured_token
    return bool(configured and provided_token) and hmac.compare_digest(provided_token, configured)


def development_review_session_enabled(environment=None, configured_token=None, hostname=None):
    """Permit HttpOnly Admin bootstrap only for localhost in explicit non-production environments."""
    current_environment = os.getenv("ROOTMEDICALS_ENV", "") if environment is None else environment
    configured = os.getenv("LLMEBM_REVIEW_ADMIN_TOKEN", "") if configured_token is None else configured_token
    local_host = str(hostname or "").strip().lower().rstrip(".") in {"localhost", "127.0.0.1", "::1"}
    return (
        str(current_environment or "").strip().lower() in {"local", "dev", "development", "test"}
        and bool(configured)
        and local_host
    )


def hierarchy_admin_request_authorized(provided_token, configured_token=None):
    """Hierarchy mutations require an independently rotatable secret until formal RBAC is installed."""
    configured = os.getenv("LLMEBM_HIERARCHY_ADMIN_TOKEN", "") if configured_token is None else configured_token
    return bool(configured and provided_token) and hmac.compare_digest(provided_token, configured)


def validate_review_generation_payload(slot_ids, *, force=False, require_current_scope_review=True):
    """Return the one UI slot or reject batch, force, and mapping-review bypass attempts."""
    normalized = [str(slot_id or "").strip() for slot_id in (slot_ids or [])]
    if force or not require_current_scope_review or len(normalized) != 1 or not normalized[0]:
        raise ValueError(
            "Review UI generation requires exactly one explicit slot, current scope review, and force=false."
        )
    return normalized[0]


def validate_review_generation_status(status):
    """Reject reviewer-token spend on current content before retrieval or LLM work starts."""
    normalized = str(status or "").strip().lower()
    if normalized not in {"empty", "stale", "failed"}:
        raise ValueError(
            f"Review UI generation only accepts empty, stale, or failed slots; current status is {normalized or 'unknown'}."
        )
    return normalized


def mutation_rate_allowed(key, *, now=None, limit=30, window_seconds=60):
    """Bound mutation bursts in one process; a distributed limiter replaces this for multi-instance runtime."""
    current = time.monotonic() if now is None else float(now)
    normalized = str(key or "").strip()
    if not normalized or limit < 1 or window_seconds < 1:
        return False
    cutoff = current - window_seconds
    with _RATE_LOCK:
        recent = [stamp for stamp in _MUTATION_WINDOWS.get(normalized, []) if stamp > cutoff]
        if len(recent) >= limit:
            _MUTATION_WINDOWS[normalized] = recent
            return False
        recent.append(current)
        _MUTATION_WINDOWS[normalized] = recent
    return True
