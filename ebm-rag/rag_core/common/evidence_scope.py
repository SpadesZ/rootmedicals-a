# 模組定位: ebm-rag ingestion 與 Topic revision 共用的 evidence scope 正規化器。
# 主要責任: 將疾病/topic 與完整 slot ID 轉成穩定、可索引的 topic_key/slot_key。
# 呼叫來源: Core1 metadata、SQLite state repository、Core5 Topic revision schema/API。
# 輸入契約: 未可信 scalar topic 值、slot key/完整 slot ID，以及 chunk payload metadata。
# 輸出契約: 小寫 kebab-case topic key、universal/custom slot key，或保守 wildcard "*"。
# 安全邊界: 不以 LLM 或文字啟發式猜臨床分類；缺失/非法 scope 一律降為 global wildcard。
# 維護提醒: wildcard 是 fail-safe；若要更精準，應由 ingestion caller 明確提供 slot_keys。
# ----------------------------------------------------------------------------------------------------

import re
import unicodedata


_SLOT_KEY_PATTERN = re.compile(r"^(?:universal|custom):[a-zA-Z0-9_.:-]{1,140}$")
_UNKNOWN_VALUES = {"", "*", "none", "null", "unknown", "unspecified"}


def canonical_topic_key(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if text in _UNKNOWN_VALUES:
        return "*"
    text = re.sub(r"[\W_]+", "-", text, flags=re.UNICODE).strip("-")
    return text[:160] or "*"


def slot_key_from_id(value) -> str:
    text = str(value or "").strip()
    if text == "*":
        return "*"
    for namespace in ("universal", "custom"):
        marker = f":{namespace}:"
        if marker in text:
            text = namespace + ":" + text.split(marker, 1)[1]
            break
    return text if _SLOT_KEY_PATTERN.fullmatch(text) else "*"


def normalize_slot_keys(value) -> list[str]:
    if not isinstance(value, list):
        return ["*"]
    normalized = list(dict.fromkeys(slot_key_from_id(item) for item in value))
    if not normalized or "*" in normalized:
        return ["*"]
    return normalized[:200]


def normalize_evidence_scope(payload: dict | None) -> tuple[str, list[str]]:
    payload = payload if isinstance(payload, dict) else {}
    topic_key = canonical_topic_key(payload.get("topic_key") or payload.get("disease"))
    slot_keys = normalize_slot_keys(payload.get("slot_keys"))
    if topic_key == "*":
        slot_keys = ["*"]
    return topic_key, slot_keys
