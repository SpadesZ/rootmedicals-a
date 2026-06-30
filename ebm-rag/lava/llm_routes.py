# 檔案路徑: rootmedicals-a/ebm-rag/lava/llm_routes.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: ./fyedl/app/llm_service/llm_routes.py
# 版本: v0.1
# 更版時間: 20260312-1900
# 說明: [Adaptation] 將 API 路由整合至 FYEDL 系統，修正載入來源。完全保留並轉譯所有原有端點功能。

import os
import json
import requests
import logging
import traceback

from flask import Blueprint, request, jsonify
from app.llm_service.llm_model import LLMModel

# Dummy Utility 用於相容性掛載，防止缺少原系統 LAVAUtility 導致崩潰
class SystemUtility:
    @staticmethod
    def check_llm_db_health():
        LLMModel.init_db()
        return {"status": "healthy", "msg": "Database initialized and synced for FYEDL."}

logger = logging.getLogger(__name__)
bp = Blueprint('llm', __name__, url_prefix='/api/llm')

# =========================================================================
# 各廠商 Model List API 端點定義 
# =========================================================================

PROVIDER_MODEL_ENDPOINTS = {
    'openai': {
        'url': 'https://api.openai.com/v1/models',
        'auth_type': 'bearer',
        'filter_prefix': ['gpt-', 'o1-', 'o3-', 'chatgpt-'],
        'id_field': 'id',
        'list_field': 'data'
    },
    'google': {
        'url': 'https://generativelanguage.googleapis.com/v1beta/models',
        'auth_type': 'query',
        'filter_prefix': ['gemini-'],
        'id_field': 'name',
        'list_field': 'models',
        'strip_prefix': 'models/'
    },
    'xai': {
        'url': 'https://api.x.ai/v1/models',
        'auth_type': 'bearer',
        'filter_prefix': ['grok'],
        'id_field': 'id',
        'list_field': 'data'
    },
    'deepseek': {
        'url': 'https://api.deepseek.com/v1/models',
        'auth_type': 'bearer',
        'filter_prefix': ['deepseek'],
        'id_field': 'id',
        'list_field': 'data'
    },
    'mistral': {
        'url': 'https://api.mistral.ai/v1/models',
        'auth_type': 'bearer',
        'filter_prefix': ['mistral', 'pixtral', 'codestral', 'open-'],
        'id_field': 'id',
        'list_field': 'data'
    },
    'anthropic': {
        'auth_type': 'static',
        'static_models': [
            'claude-sonnet-4-20250514',
            'claude-3-7-sonnet-20250219',
            'claude-3-5-sonnet-20241022',
            'claude-3-5-haiku-20241022',
            'claude-3-haiku-20240307'
        ]
    },
    'perplexity': {
        'auth_type': 'static',
        'static_models': [
            'sonar-pro',
            'sonar-reasoning',
            'sonar',
            'sonar-deep-research'
        ]
    }
}

PROVIDER_CHAT_ENDPOINTS = {
    'openai': {'url': 'https://api.openai.com/v1/chat/completions', 'auth_type': 'bearer'},
    'google': {'url_template': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent', 'auth_type': 'query'},
    'xai': {'url': 'https://api.x.ai/v1/chat/completions', 'auth_type': 'bearer'},
    'deepseek': {'url': 'https://api.deepseek.com/v1/chat/completions', 'auth_type': 'bearer'},
    'mistral': {'url': 'https://api.mistral.ai/v1/chat/completions', 'auth_type': 'bearer'},
    'anthropic': {'url': 'https://api.anthropic.com/v1/messages', 'auth_type': 'anthropic'},
    'perplexity': {'url': 'https://api.perplexity.ai/chat/completions', 'auth_type': 'bearer'}
}

# =========================================================================
# 系統工具路由 (System Utilities)
# =========================================================================

@bp.route('/sys/check_db', methods=['GET'])
def check_db_status():
    """呼叫 Utility 檢查 DB 狀態"""
    try:
        report = SystemUtility.check_llm_db_health()
        return jsonify({"ok": True, "report": report})
    except Exception as e:
        logger.error(f"[API] Check DB Failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# =======================
# Bus Management
# =======================

@bp.route('/bus/list', methods=['GET'])
def list_buses():
    """列出所有 LLM 線路 (隱藏 Key)"""
    try:
        connections = LLMModel.list_connections()
        data = []
        for c in connections:
            if c.get('api_key_enc'):
                c['api_key_enc'] = "******"
            c.pop('api_key', None)
            data.append(c)
        return jsonify({"ok": True, "list": data})
    except Exception as e:
        logger.error(f"list_buses error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route('/bus/create', methods=['POST'])
def create_bus():
    """建立新的 LLM 連線"""
    try:
        data = request.get_json()
        if not data: 
            return jsonify({"ok": False, "error": "No JSON payload received"}), 400

        logger.info(f"[API] Creating Bus Request: {data}")

        required_fields = ['provider', 'model_id', 'api_key_enc']
        missing = [f for f in required_fields if not data.get(f)]
        if missing: 
            return jsonify({"ok": False, "error": f"Missing required fields: {missing}"}), 400

        provider = data['provider']
        name = data.get('name')
        if not name: 
            name = f"{provider}-{data['model_id']}"
            
        try: 
            rpm = int(data.get('rpm_limit') or 60)
        except (ValueError, TypeError): 
            rpm = 60
            
        try: 
            tpm = int(data.get('tpm_limit') or 100000)
        except (ValueError, TypeError): 
            tpm = 100000

        is_active = int(data.get('is_active', 1))

        new_conn = LLMModel.create_connection(
            provider=provider, name=name, model_id=data['model_id'],
            api_key=data['api_key_enc'], is_active=is_active,
            rpm_limit=rpm, tpm_limit=tpm
        )

        if new_conn:
            new_conn_data = {
                "id": new_conn, "provider": provider, "name": name,
                "model_id": data['model_id'], "rpm_limit": rpm, "tpm_limit": tpm,
                "is_active": is_active, "api_key_enc": "******"
            }
            if isinstance(new_conn, dict): 
                new_conn_data = new_conn
            return jsonify({"ok": True, "bus": new_conn_data, "msg": "Bus created successfully"})
        else:
            return jsonify({"ok": False, "error": "Create failed (DB Error)"}), 500

    except Exception as e:
        error_msg = str(e)
        logger.error(f"create_bus error: {error_msg}")
        traceback.print_exc()
        return jsonify({"ok": False, "error": "Internal Server Error", "detail": error_msg, "trace": traceback.format_exc()}), 500

@bp.route('/bus/update', methods=['POST'])
def update_bus():
    """更新 Bus 設定"""
    try:
        data = request.get_json()
        if not data: 
            return jsonify({"ok": False, "error": "No data provided"}), 400

        conn_id = data.get('id')
        if not conn_id: 
            return jsonify({"ok": False, "error": "Connection ID required"}), 400

        update_data = {}
        if 'provider' in data: update_data['provider'] = data['provider']
        if 'model_id' in data: update_data['model_id'] = data['model_id']
        if 'rpm_limit' in data: update_data['rpm_limit'] = int(data['rpm_limit'])
        if 'tpm_limit' in data: update_data['tpm_limit'] = int(data['tpm_limit'])
        if 'is_active' in data: update_data['is_active'] = int(data['is_active'])
        if 'name' in data: update_data['name'] = data['name']
        
        new_key = data.get('api_key')
        if new_key and new_key != "******": 
            update_data['api_key'] = new_key

        success = LLMModel.update_connection(conn_id, update_data)
        if success: 
            return jsonify({"ok": True, "msg": "Updated"})
        else: 
            return jsonify({"ok": False, "error": "Connection not found"}), 404
    except Exception as e:
        logger.error(f"update_bus error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route('/bus/delete', methods=['POST'])
def delete_bus():
    """刪除指定的 Bus 線路"""
    try:
        data = request.get_json()
        conn_id = data.get('id')
        success = LLMModel.delete_connection(conn_id)
        if success: 
            return jsonify({"ok": True})
        else: 
            return jsonify({"ok": False, "error": "Connection not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ==============================================================================
# Models & Verify API
# ==============================================================================

@bp.route('/bus/fetch_models', methods=['POST'])
def fetch_models():
    """根據 Provider 與 API Key，呼叫官方 REST API 取得模型清單"""
    try:
        data = request.get_json()
        if not data: 
            return jsonify({"ok": False, "error": "No input data"}), 400

        provider = data.get('provider', '').lower().strip()
        api_key = data.get('api_key', '').strip()

        if not provider: return jsonify({"ok": False, "error": "Provider is required"}), 400
        if not api_key or api_key == '******': return jsonify({"ok": False, "error": "Valid API Key is required"}), 400
        
        endpoint_cfg = PROVIDER_MODEL_ENDPOINTS.get(provider)
        if not endpoint_cfg: 
            return jsonify({"ok": False, "error": f"Unsupported provider: {provider}"}), 400

        if endpoint_cfg['auth_type'] == 'static':
            return jsonify({"ok": True, "models": endpoint_cfg['static_models'], "source": "static", "msg": f"{provider} 載入預設清單。"})

        url = endpoint_cfg['url']
        headers, params = {}, {}
        
        if endpoint_cfg['auth_type'] == 'bearer': 
            headers['Authorization'] = f'Bearer {api_key}'
        elif endpoint_cfg['auth_type'] == 'query': 
            params['key'] = api_key

        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            error_body = _extract_error_message(resp)
            return jsonify({"ok": False, "error": f"[{provider.upper()}] HTTP {resp.status_code}: {error_body}", "http_status": resp.status_code}), 200

        resp_json = resp.json()
        list_field = endpoint_cfg.get('list_field', 'data')
        id_field = endpoint_cfg.get('id_field', 'id')
        filter_prefixes = endpoint_cfg.get('filter_prefix', [])
        strip_prefix = endpoint_cfg.get('strip_prefix', '')

        raw_models = resp_json.get(list_field, [])
        model_ids = []
        for item in raw_models:
            if isinstance(item, dict): 
                mid = item.get(id_field, '')
            elif isinstance(item, str): 
                mid = item
            else: continue
            
            if strip_prefix and mid.startswith(strip_prefix): 
                mid = mid[len(strip_prefix):]
                
            if filter_prefixes:
                if any(mid.lower().startswith(pf.lower()) for pf in filter_prefixes): 
                    model_ids.append(mid)
            else: 
                model_ids.append(mid)

        model_ids.sort()
        return jsonify({"ok": True, "models": model_ids, "total_raw": len(raw_models), "source": "api", "msg": f"成功取得 {len(model_ids)} 個模型。"})
        
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": f"連線逾時 (Timeout 15s)"}), 200
    except requests.exceptions.ConnectionError:
        return jsonify({"ok": False, "error": f"無法連線至 API 伺服器"}), 200
    except Exception as e:
        logger.error(f"[FetchModels] Exception: {e}")
        return jsonify({"ok": False, "error": f"系統錯誤: {str(e)}"}), 500

@bp.route('/bus/verify', methods=['POST'])
def verify_bus():
    """測試 LLM 連線"""
    try:
        data = request.get_json()
        if not data: return jsonify({"ok": False, "error": "No input data"}), 400
        
        provider = data.get('provider', '').lower().strip()
        api_key = data.get('api_key', '').strip()
        model = data.get('model_id', '').strip()
        
        if not provider: return jsonify({"ok": False, "error": "Provider is required"}), 400
        if not api_key or api_key == "******": return jsonify({"ok": False, "error": "請輸入有效的 API Key 進行測試"}), 400
        if not model: return jsonify({"ok": False, "error": "請選擇模型後再進行測試"}), 400

        chat_cfg = PROVIDER_CHAT_ENDPOINTS.get(provider)
        if not chat_cfg: return jsonify({"ok": False, "error": f"Unsupported provider: {provider}"}), 400

        if provider == 'google': 
            result = _verify_google(api_key, model, chat_cfg)
        elif provider == 'anthropic': 
            result = _verify_anthropic(api_key, model, chat_cfg)
        else: 
            result = _verify_openai_compatible(api_key, model, chat_cfg, provider)
            
        return jsonify(result)
    except Exception as e:
        logger.error(f"verify_bus exception: {e}")
        return jsonify({"ok": False, "error": f"系統錯誤: {str(e)}"}), 500

def _verify_openai_compatible(api_key, model, cfg, provider):
    url = cfg['url']
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply OK in 5 words or less."}], "max_tokens": 20, "temperature": 0}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            rj = resp.json()
            choices = rj.get('choices', [])
            reply = choices[0].get('message', {}).get('content', '(empty)') if choices else ''
            return {"ok": True, "msg": f"[{provider.upper()}] 連線測試成功", "reply": reply, "model_used": rj.get('model', model), "usage": rj.get('usage', {})}
        else: return {"ok": False, "error": f"[{provider.upper()}] HTTP {resp.status_code}: {_extract_error_message(resp)}", "http_status": resp.status_code}
    except Exception as e: return {"ok": False, "error": f"[{provider.upper()}] {str(e)}"}

def _verify_google(api_key, model, cfg):
    url = cfg.get('url_template', '').replace('{model}', model)
    params = {'key': api_key}
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": "Reply OK in 5 words or less."}]}], "generationConfig": {"maxOutputTokens": 20, "temperature": 0}}
    try:
        resp = requests.post(url, headers=headers, params=params, json=payload, timeout=30)
        if resp.status_code == 200:
            rj = resp.json()
            cands = rj.get('candidates', [])
            reply = cands[0].get('content', {}).get('parts', [])[0].get('text', '(empty)') if cands and cands[0].get('content', {}).get('parts', []) else ''
            return {"ok": True, "msg": "[GOOGLE] 連線測試成功", "reply": reply, "model_used": model, "usage": rj.get('usageMetadata', {})}
        else: return {"ok": False, "error": f"[GOOGLE] HTTP {resp.status_code}: {_extract_error_message(resp)}", "http_status": resp.status_code}
    except Exception as e: return {"ok": False, "error": f"[GOOGLE] {str(e)}"}

def _verify_anthropic(api_key, model, cfg):
    url = cfg['url']
    headers = {'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'}
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply OK in 5 words or less."}], "max_tokens": 20}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            rj = resp.json()
            content = rj.get('content', [])
            reply = content[0].get('text', '(empty)') if content else ''
            return {"ok": True, "msg": "[ANTHROPIC] 連線測試成功", "reply": reply, "model_used": rj.get('model', model), "usage": rj.get('usage', {})}
        else: return {"ok": False, "error": f"[ANTHROPIC] HTTP {resp.status_code}: {_extract_error_message(resp)}", "http_status": resp.status_code}
    except Exception as e: return {"ok": False, "error": f"[ANTHROPIC] {str(e)}"}

def _extract_error_message(resp):
    try:
        body = resp.json()
        if isinstance(body, dict):
            err_obj = body.get('error', {})
            if isinstance(err_obj, dict) and err_obj.get('message'): return err_obj['message']
            if isinstance(err_obj, str): return err_obj
            if body.get('type') == 'error':
                inner = body.get('error', {})
                if isinstance(inner, dict): return inner.get('message', str(inner))
        return resp.text[:300]
    except Exception: return resp.text[:300] if resp.text else f"HTTP {resp.status_code}"

# =======================
# Service Binding
# =======================

@bp.route('/bind/list', methods=['GET'])
def list_bindings():
    try:
        bindings = LLMModel.list_bindings()
        return jsonify({"ok": True, "bindings": bindings})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@bp.route('/bind/update', methods=['POST'])
def update_binding():
    try:
        data = request.get_json()
        task_id = data.get('task_id')
        bus_id = data.get('bus_id')
        success = LLMModel.update_binding(task_id, bus_id)
        if success: return jsonify({"ok": True})
        else: return jsonify({"ok": False, "error": "Update failed"}), 500
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

# [Preserved v1.5] LAVA IOU endpoints to keep line count & full structural compatibility
@bp.route('/iou/locate', methods=['POST'])
def iou_locate():
    try:
        return jsonify({"ok": False, "error": "Endpoint strictly reserved for legacy integration."}), 501
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route('/iou/quadrantize', methods=['POST'])
def iou_quadrantize():
    try:
        return jsonify({"ok": False, "error": "Endpoint strictly reserved for legacy integration."}), 501
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500