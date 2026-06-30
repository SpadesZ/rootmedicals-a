# 檔案路徑: rootmedicals-a/ebm-rag/lava/adapter/__init__.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

from lava.adapter.openai_compatible import OpenAICompatibleAdapter
from lava.adapter.google import GoogleAdapter
from lava.adapter.anthropic import AnthropicAdapter

_ADAPTERS = {
    "openai": OpenAICompatibleAdapter("openai", "https://api.openai.com/v1"),
    "xai": OpenAICompatibleAdapter("xai", "https://api.x.ai/v1"),
    "deepseek": OpenAICompatibleAdapter("deepseek", "https://api.deepseek.com/v1"),
    "mistral": OpenAICompatibleAdapter("mistral", "https://api.mistral.ai/v1"),
    "openrouter": OpenAICompatibleAdapter("openrouter", "https://openrouter.ai/api/v1"),
    "google": GoogleAdapter(),
    "anthropic": AnthropicAdapter(),
}


def get_adapter(provider: str):
    return _ADAPTERS.get(provider.lower().strip())
