# 檔案路徑: rootmedicals-a/ebm-rag/lava/llm_bus.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: ./fyedl/app/llm_service/llm_bus.py
# 版本: v1.2
# 更版時間: 20260502-1200
# 說明: 
# 1. [Feature] 確保 OpenAI 路由正確指向新實作的 `llm_openai` (SDK 版)。
# 2. [Inherit] 完整保留 v1.1 中所有的防護機制與代理介面。

import importlib
import logging
from typing import Any, Dict, List, Optional, Tuple

# 引入配置與資料庫模型
from app.config_manager import ConfigManager
try:
    from app.llm_service.llm_model import LLMModel
except ImportError:
    LLMModel = None

# 設定模組專屬日誌
logger = logging.getLogger("LlmBus_Engine")
logger.setLevel(logging.INFO)

class LlmBus:
    """
    FYEDL LLM 服務匯流排 (v1.2 代理介面完整版)
    負責管理特定 Bus ID 的 Provider 加載、連線檢查與通訊轉發。
    內建冷啟動防護、介面適應能力與模型清單轉發。
    """
    def __init__(self):
        self._client = None
        self._provider_name = None
        self._model_name = None
        self._bus_id = None

    def _load_driver(self, provider: str) -> Tuple[Any, str]:
        """
        [Dynamic Driver Loader]
        依據 provider 字串動態匯入對應模組。
        """
        mapping = {
            "google": ("llm_google", "GoogleClient"),
            "openai": ("llm_openai", "OpenAIClient"), # 🌟 確認指向新的 llm_openai.py
            "mistral": ("llm_mistral", "MistralClient"), 
            "anthropic": ("llm_anthropic", "AnthropicClient"),
            "xai": ("llm_grok", "GrokClient"), 
            "openrouter": ("llm_openrouter", "OpenRouterClient"),
        }
        
        prov = provider.lower().strip()
        
        if prov == "perplexity":
            logger.error("[LlmBus] Perplexity provider is no longer supported. Please switch to OpenRouter.")
            return None, "Provider 'perplexity' has been deprecated and removed."
        
        if prov not in mapping:
            logger.warning(f"[LlmBus] Attempted to load unsupported provider: {prov}")
            return None, f"Unknown provider: {prov}"

        module_name, class_name = mapping[prov]
        try:
            module = importlib.import_module(f"app.llm_service.adapter.{module_name}")
            client_class = getattr(module, class_name)
            logger.info(f"[LlmBus] Successfully mapped {prov} -> {module_name}.{class_name}")
            return client_class, ""
        except ImportError as e:
            logger.critical(f"[LlmBus] Failed to import module {module_name}: {e}")
            return None, f"Module import failed: {str(e)}"
        except AttributeError as e:
            logger.critical(f"[LlmBus] Class {class_name} not found in {module_name}: {e}")
            return None, f"Adapter class not found: {str(e)}"

    def set_provider(self, provider: str, api_key: str, model_id: str = None) -> Tuple[bool, str]:
        """ 
        切換並初始化 Provider 線路 
        保證無論成功或失敗，皆回傳 (狀態布林值, 訊息字串)，避免 GUI 解包錯誤。
        """
        client_class, err = self._load_driver(provider)
        
        if client_class:
            try:
                # 嘗試實例化 Adapter
                self._client = client_class(api_key, model_id)
                self._provider_name = provider
                self._model_name = model_id
                
                success_msg = f"Bus initialized with {provider} ({model_id})"
                logger.info(f"[LlmBus] {success_msg}")
                return True, success_msg
                
            except Exception as e:
                # 實例化失敗 (例如 API Key 錯誤或連線逾時)
                error_msg = f"Client instantiation failed: {e}"
                logger.error(f"[LlmBus] {error_msg}")
                self._client = None
                return False, error_msg
        else:
            # 驅動程式找不到或載入失敗
            error_msg = f"Driver load error: {err}"
            logger.error(f"[LlmBus] {error_msg}")
            self._client = None
            return False, error_msg

    def load_from_config(self, bus_id: int):
        """
        [Persistence] 從資料庫或配置檔恢復連線設定
        包含資料庫防禦性自檢，防止 no such table 錯誤
        """
        self._bus_id = bus_id
        
        # 智慧冷啟動防護 (Auto-Init)
        if LLMModel:
            if hasattr(LLMModel, 'init_db'):
                try:
                    LLMModel.init_db()
                    logger.debug("[LlmBus] DB structure verified successfully prior to load.")
                except Exception as db_err:
                    logger.error(f"[LlmBus] DB initialization failed during self-check: {db_err}")

        # 優先機制: LLMModel
        try:
            if LLMModel:
                conn_data = LLMModel.get_connection(bus_id)
                if conn_data and conn_data.get('provider') and conn_data.get('api_key'):
                    self.set_provider(conn_data['provider'], conn_data['api_key'], conn_data.get('model_id'))
                    logger.info(f"[LlmBus] Successfully loaded Bus {bus_id} from SQLite DB.")
                    return
        except Exception as e:
            logger.debug(f"[LlmBus] SQLite recovery failed for Bus {bus_id}: {e}")

        # 備援機制: ConfigManager (維持與舊系統相容)
        prefix = f"LLM{bus_id}_" 
        provider = ConfigManager.get_kv(f"{prefix}PROVIDER")
        api_key = ConfigManager.get_kv(f"{prefix}API_KEY")
        model = ConfigManager.get_kv(f"{prefix}MODEL")

        if provider and api_key:
            self.set_provider(provider, api_key, model)
            logger.info(f"[LlmBus] Falling back to ConfigManager for Bus {bus_id}.")
        else:
            logger.warning(f"[LlmBus] No configuration found for Bus {bus_id} in any storage.")

    def get_available_models(self, api_key: str) -> List[str]:
        """
        [Standard Interface] 取得可用模型列表代理介面。
        解決 GUI 呼叫 `hasattr(bus, 'get_available_models')` 時找不到方法的斷鏈問題。
        動態調用已載入 Adapter 的靜態方法。
        """
        if not self._client:
            logger.error("[LlmBus] Cannot fetch models: No active client loaded. Please set provider first.")
            return []
            
        try:
            # 優先嘗試呼叫類別層級的靜態方法
            if hasattr(self._client.__class__, 'get_available_models'):
                logger.info(f"[LlmBus] Proxying fetch models request to {self._provider_name} adapter...")
                return self._client.__class__.get_available_models(api_key)
                
            # 備援：若被實作為實例方法
            elif hasattr(self._client, 'get_available_models'):
                logger.info(f"[LlmBus] Proxying fetch models request to {self._provider_name} instance...")
                return self._client.get_available_models(api_key)
                
            else:
                logger.warning(f"[LlmBus] The loaded adapter for '{self._provider_name}' does not support fetching models.")
                return []
                
        except Exception as e:
            logger.error(f"[LlmBus] Error proxying fetch models request: {e}")
            return []

    def send_message(self, text: str, images: List[str] = None) -> Tuple[bool, Dict, str]:
        """
        [Standard Interface] 發送通訊內容 (具備 Duck Typing 介面防護)
        """
        if not self._client:
            logger.error(f"[LlmBus] Send failed: Bus {self._bus_id} has no active client.")
            return False, {}, "No Provider Loaded (Check LAVA Settings)"
            
        try:
            # 雙重介面路由 (Duck Typing)
            if hasattr(self._client, 'send_message'):
                return self._client.send_message(text, images)
            elif hasattr(self._client, 'send_text_and_optional_images'):
                logger.warning(f"[LlmBus] Adapter for {self._provider_name} uses legacy method. Auto-routing...")
                return self._client.send_text_and_optional_images(text, images)
            else:
                raise AttributeError(f"The loaded adapter for '{self._provider_name}' lacks a valid transmission method.")
                
        except Exception as e:
            logger.error(f"[LlmBus] Runtime error during message dispatch: {e}")
            return False, {}, f"Runtime Dispatch Error: {str(e)}"

    def get_info(self) -> Dict[str, Any]:
        """取得當前 Bus 狀態摘要"""
        return {
            "bus_id": self._bus_id,
            "provider": self._provider_name,
            "model": self._model_name,
            "active": self._client is not None
        }