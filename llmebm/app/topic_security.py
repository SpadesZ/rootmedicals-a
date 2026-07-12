# File path: rootmedicals-a/llmebm/app/topic_security.py
# Description: Minimal authorization boundary for paid Topic generation requests.

import hmac
import ipaddress
import os


def generation_request_authorized(client_host, provided_token, configured_token=None):
    configured = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "") if configured_token is None else configured_token
    if configured:
        return bool(provided_token) and hmac.compare_digest(provided_token, configured)
    try:
        return ipaddress.ip_address(client_host or "").is_loopback
    except ValueError:
        return False
