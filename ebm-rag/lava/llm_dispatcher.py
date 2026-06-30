# 檔案路徑: rootmedicals-a/ebm-rag/lava/llm_dispatcher.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: ./fyedl/app/llm_service/llm_dispatcher.py
# 版本: v1.3
# 更版時間: 20260502-1200
# 說明: [Strict Update] 無縫接軌 OpenAI SDK 版，完美保留 v1.2 的背景分發與意圖掃描邏輯。

import json
import logging
from typing import Union, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

# Config & Bus
from app.config_manager import ConfigManager
from . import _get_bus

# 感知層 (Fast Path)
from app.add.obj_detector import ObjectDetector

# [v1.1/v1.2] 嘗試匯入新版 DB Model，完全保留原結構，不破壞舊有依賴
try:
    from app.llm_service.llm_model import LLMModel
except ImportError:
    LLMModel = None

# 記憶層 (CCMA)
try:
    from app.ccma.session_manager import SessionManager
except ImportError:
    print("[Dispatcher] Warning: Could not import 'ccma.session_manager'. Memory features disabled.")
    SessionManager = None

# 設定 Logger
logger = logging.getLogger("LLM_Dispatcher")
logger.setLevel(logging.INFO)

# [v0.8/v1.2] 初始化執行緒池 (Max Workers = 4)
# 用於處理非阻斷式任務 (如 Judge, Vision Pre-proc)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="LLM_Worker")


def _resolve_intent(prompt: str, project_id: str) -> str:
    """
    [Private Helper] 意圖解析引擎 (Hybrid Path)
    Fast Path (Regex/Keyword) -> Smart Path (LLM-6)

    Returns: 'coding' | 'general'
    """
    # 1. Fast Path (Local Regex/Keyword)
    # obj_detector 回傳格式: {'type': 'coding'|'general'|'ambiguous', 'score': int, ...}
    scan_result = ObjectDetector.scan_intent(prompt)
    intent_type = scan_result.get('type', 'general')

    # 若明確 (coding 或 general)，直接回傳，節省 Token 與時間
    if intent_type != 'ambiguous':
        return intent_type

    # 2. Smart Path (LLM-6)
    # [Lazy Import] 避免 Circular Dependency (task -> dispatcher -> task)
    logger.info(f"[Dispatcher] Intent ambiguous. Triggering Smart Path (LLM-6) for: {prompt[:20]}...")
    try:
        from app.llm_service.matching_tasks import task_intent_scan

        # 調用 LLM-6 進行深度分析
        smart_result = task_intent_scan.execute_scan(prompt, project_id)
        # task_intent_scan 回傳: {'type': '...', 'reason': '...'}
        return smart_result.get('type', 'general')

    except ImportError:
        logger.warning("[Dispatcher] task_intent_scan module not found. Falling back to 'general'.")
        return 'general'
    except Exception as e:
        logger.error(f"[Dispatcher] Smart Path failed: {e}. Falling back to 'general'.")
        return 'general'


def dispatch_background_task(task_id: str, *args, **kwargs):
    """
    [v0.8/v1.2] 背景任務分發器 (Fire-and-Forget)
    將任務提交給 ThreadPoolExecutor，不等待結果。
    適用於: CCMA Judge, Logging, Heavy Analytics
    """
    logger.info(f"[Dispatcher] Submitting background task: {task_id}")

    try:
        # 動態路由與導入，避免循環依賴
        func = None

        if task_id == "task_ccma_judge":
            from app.llm_service.matching_tasks import task_ccma_judge
            func = task_ccma_judge.execute_judge

        # 未來可擴充其他背景任務 (e.g., task_vision_proc)

        if func:
            _executor.submit(func, *args, **kwargs)
        else:
            logger.warning(f"[Dispatcher] Unknown background task ID: {task_id}")

    except ImportError as e:
        logger.error(f"[Dispatcher] Background Import Error: {e}")
    except Exception as e:
        logger.error(f"[Dispatcher] Background Submission Error: {e}")


def dispatch_task(task_id: str,
                  prompt: str,
                  attachments: List[str] = None,
                  project_id: str = "default") -> Union[str, Dict[str, Any]]:
    """
    [Core] 任務分發器 (Central Dispatcher)
    負責將請求路由至正確的 LLM Bus，並處理 Context 注入與記憶寫入。

    Args:
        task_id (str): 任務代號 (e.g. 'task_assist_chat', 'task_code_review')
        prompt (str): 使用者輸入或系統組裝後的 Prompt
        attachments (List[str]): 檔案路徑列表
        project_id (str): 專案 ID (用於記憶檢索與寫入)

    Returns:
        str: LLM 回傳的文字內容 (成功時)
        dict: {'ok': False, 'msg': ...} (失敗時)
    """

    # --- 1. 意圖路由 (Intent Routing) ---
    # 僅針對一般對話任務進行意圖偵測，若是一般閒聊(general)則可能不需要掛載 heavy context
    # 這裡預留擴充點，目前主要由 Task 層決定 Context，Dispatcher 負責執行

    # --- 2. 檔案處理 (Payload Routing) ---
    # 利用 ObjectDetector 分析檔案類型，雖然 Bus 層會處理，但這裡可做預先檢查
    # 目前邏輯：直接透傳給 Bus，由 Bus Adapter 處理多模態封裝
    all_files = attachments if attachments else []

    # --- 3. 服務查找 (Service Lookup) ---
    bus_id_str = None
    
    # [v1.1/v1.2] 優先從新的 LLMModel 資料庫查詢綁定 (純新增邏輯，絕不刪減舊代碼)
    if LLMModel:
        try:
            binding = LLMModel.get_binding_for_task(task_id)
            if binding and binding.get('connection_id'):
                bus_id_str = str(binding['connection_id'])
                logger.debug(f"[Dispatcher] Found binding in DB for {task_id}: {bus_id_str}")
        except Exception as e:
            logger.error(f"[Dispatcher] DB Lookup Error: {e}")

    # 查詢該 Task 綁定到哪個 Bus ID (若 DB 中無資料，無縫回退至原本的 ConfigManager)
    if not bus_id_str:
        bus_id_str = ConfigManager.get_kv(f"SERVICE_MAP_{task_id}")

    if not bus_id_str:
        err_msg = f"No service bound for task: {task_id}"
        logger.error(err_msg)
        return {"ok": False, "msg": err_msg}

    try:
        bus_id = int(bus_id_str)
    except ValueError:
        return {"ok": False, "msg": f"Invalid Bus ID format: {bus_id_str}"}

    # --- 4. 取得 Bus (Execution) ---
    bus = _get_bus(bus_id)
    if not bus:
        return {"ok": False, "msg": f"Failed to initialize Bus-{bus_id}."}

    # --- 5. 執行發送 (Send Request) ---
    logger.info(f"[Dispatcher] Routing '{task_id}' to Bus-{bus_id}...")
    success, response, error_msg = bus.send_message(prompt, images=all_files)

    if success:
        text = response.get("text", "")
        if not text:
            # [v1.2 OpenRouter Enhancement] 增加空載體警告追蹤
            logger.warning(f"[Dispatcher] Provider for Bus-{bus_id} returned empty content.")
            return {"ok": False, "msg": "Provider returned empty text."}

        # --- 6. 記憶寫入 (Memory Commit) ---
        # 僅在 SessionManager 有效且為對話任務 (task_assist_chat) 時執行
        # task_codegen_assist 通常使用 'task_assist_chat' 作為路由 Key
        if task_id == "task_assist_chat" and SessionManager:
            try:
                # [v0.7 Update] 使用 get_latest_tid 確保歸檔正確
                tid = SessionManager.get_latest_tid(project_id)

                # 簡易判斷是否包含代碼塊
                raw_code = text if "```" in text else ""

                # 寫入 MinorFrame
                # [v0.8 Update] 接收回傳的 SID，用於後續裁決
                sid = SessionManager.create_minor_frame(
                    tid=tid,
                    prompt=prompt[-200:] + "...",  # 僅記錄末端以節省空間，或視需求記錄完整
                    attachments=all_files,
                    response=text,
                    raw_code=raw_code
                )

                if sid:
                    logger.info(f"[Dispatcher] Memory committed to TID: {tid}, SID: {sid}")

                    # [v0.8 New] 觸發非同步裁決 (Async Judge)
                    # 這是 CCMA 架構的核心：Parallel Feedback Loop
                    dispatch_background_task(
                        "task_ccma_judge",
                        sid=sid,
                        prompt=prompt,
                        response=text,
                        project_id=project_id
                    )

            except Exception as e:
                logger.error(f"[Dispatcher] Memory commit failed: {e}")
                # 不阻擋回傳，僅記錄錯誤

        return text

    else:
        # 失敗處理
        logger.error(f"[Dispatcher] Bus Execution Failed: {error_msg}")
        return {"ok": False, "msg": f"LLM Error: {error_msg}"}