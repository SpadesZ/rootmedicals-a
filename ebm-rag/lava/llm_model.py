# 檔案路徑: rootmedicals-a/ebm-rag/lava/llm_model.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG 內部 LAVA LLM 控制層，負責 provider、任務綁定與任務執行。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/lava/llm_model.py
# Timestamp: 2026-06-08
# Version: v0.7
# Description: LAVA SQLite data layer for RAG LLM connections and task bindings.
#              Adds capability-specific verification and public/secret formatting boundaries for LAVA connections.
# ----------------------------------------------------------------------------------------------------

import os
import sqlite3
import logging
from datetime import datetime

from lava.task_registry import RAG_TASKS

logger = logging.getLogger(__name__)

class LLMModel:
    """
    FYEDL 專用微型 ORM
    負責管理 data/sys/llm_match.db，儲存 LLM 連線設定與任務綁定關係。
    
    [v0.2/v0.3/v0.4] 完整 CRUD 資料層:
    === Connection (llm_connections) ===
    - create_connection: 建立新線路
    - get_connection: 取得單一線路
    - list_connections: 列出所有線路
    - update_connection: 更新線路設定
    - delete_connection: 刪除線路
    
    === Binding (task_bindings) ===
    - get_binding_for_task: 取得任務綁定
    - list_bindings: 列出所有綁定
    - update_binding: 更新/新增綁定
    - delete_binding: 解除綁定
    """
    
    # =========================================================
    # RAG 系統 Matching Tasks 白名單 (由 task_registry 驅動)
    # =========================================================
    DEFAULT_TASKS = [task["task_id"] for task in RAG_TASKS]

    # =========================================================
    # 1. 基礎設施 (Infrastructure)
    # =========================================================

    @staticmethod
    def get_db_path():
        # ebm-rag/data/sys/database/llm_match.db
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys_dir = os.path.join(base_dir, 'data', 'sys', 'database')
        os.makedirs(sys_dir, exist_ok=True)
        return os.path.join(sys_dir, 'llm_match.db')

    @staticmethod
    def _get_conn():
        path = LLMModel.get_db_path()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def init_db():
        path = LLMModel.get_db_path()
        conn = sqlite3.connect(path)
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS llm_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT 'openai',
                model_id TEXT DEFAULT '',
                api_key TEXT DEFAULT '',
                available_models TEXT DEFAULT '',
                is_active INTEGER DEFAULT 0,
                verify_status TEXT DEFAULT 'unverified',
                verified_capability TEXT DEFAULT '',
                verified_at TIMESTAMP,
                last_error TEXT DEFAULT '',
                rpm_limit INTEGER DEFAULT 60,
                tpm_limit INTEGER DEFAULT 1000000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        existing_cols = LLMModel._get_column_names(c, 'llm_connections')
        if 'vendor' in existing_cols and 'provider' not in existing_cols:
            logger.info(">>> [FYEDL] Migrating: vendor → provider")
            c.execute("ALTER TABLE llm_connections RENAME COLUMN vendor TO provider")
            conn.commit()
        if 'model_name' in existing_cols and 'model_id' not in existing_cols:
            logger.info(">>> [FYEDL] Migrating: model_name → model_id")
            c.execute("ALTER TABLE llm_connections RENAME COLUMN model_name TO model_id")
            conn.commit()
        if 'is_active' not in existing_cols:
            logger.info(">>> [FYEDL] Migrating: Adding 'is_active'")
            c.execute("ALTER TABLE llm_connections ADD COLUMN is_active INTEGER DEFAULT 0")
            if 'status' in existing_cols:
                c.execute("UPDATE llm_connections SET is_active = 1 WHERE status = 'active'")
            conn.commit()
        if 'rpm_limit' not in existing_cols:
            logger.info(">>> [FYEDL] Migrating: Adding 'rpm_limit'")
            c.execute("ALTER TABLE llm_connections ADD COLUMN rpm_limit INTEGER DEFAULT 60")
            conn.commit()
        if 'tpm_limit' not in existing_cols:
            logger.info(">>> [FYEDL] Migrating: Adding 'tpm_limit'")
            c.execute("ALTER TABLE llm_connections ADD COLUMN tpm_limit INTEGER DEFAULT 1000000")
            conn.commit()
        if 'verify_status' not in existing_cols:
            logger.info(">>> [LAVA] Migrating: Adding 'verify_status'")
            c.execute("ALTER TABLE llm_connections ADD COLUMN verify_status TEXT DEFAULT 'unverified'")
            conn.commit()
        if 'verified_capability' not in existing_cols:
            logger.info(">>> [LAVA] Migrating: Adding 'verified_capability'")
            c.execute("ALTER TABLE llm_connections ADD COLUMN verified_capability TEXT DEFAULT ''")
            conn.commit()
        if 'verified_at' not in existing_cols:
            logger.info(">>> [LAVA] Migrating: Adding 'verified_at'")
            c.execute("ALTER TABLE llm_connections ADD COLUMN verified_at TIMESTAMP")
            conn.commit()
        if 'last_error' not in existing_cols:
            logger.info(">>> [LAVA] Migrating: Adding 'last_error'")
            c.execute("ALTER TABLE llm_connections ADD COLUMN last_error TEXT DEFAULT ''")
            conn.commit()

        c.execute('''
            CREATE TABLE IF NOT EXISTS task_bindings (
                task_id TEXT PRIMARY KEY,
                connection_id INTEGER,
                is_locked BOOLEAN DEFAULT 0,
                FOREIGN KEY(connection_id) REFERENCES llm_connections(id)
            )
        ''')

        binding_cols = LLMModel._get_column_names(c, 'task_bindings')
        if 'is_locked' not in binding_cols:
            logger.info(">>> [FYEDL] Migrating Schema: Adding 'is_locked' to task_bindings")
            c.execute("ALTER TABLE task_bindings ADD COLUMN is_locked BOOLEAN DEFAULT 0")
            conn.commit()
        
        default_tasks = LLMModel.DEFAULT_TASKS
        try:
            if default_tasks:
                placeholders = ','.join(['?'] * len(default_tasks))
                delete_query = f"DELETE FROM task_bindings WHERE task_id NOT IN ({placeholders})"
                c.execute(delete_query, default_tasks)
        except Exception as e:
            logger.warning(f">>> [FYEDL] Data Cleaning Error: {e}")

        for task in default_tasks:
            c.execute('INSERT OR IGNORE INTO task_bindings (task_id, connection_id) VALUES (?, NULL)', (task,))

        conn.commit()
        conn.close()
        logger.info(">>> [FYEDL] llm_match.db initialized successfully.")

    @staticmethod
    def _get_column_names(cursor, table_name):
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]

    # =========================================================
    # 2. Connection CRUD (llm_connections 表)
    # =========================================================

    @staticmethod
    def create_connection(provider='openai', model_id='gpt-4o', name='',
                          api_key='', rpm_limit=60, tpm_limit=1000000, is_active=0):
        try:
            query = """
                INSERT INTO llm_connections 
                (name, provider, model_id, api_key, is_active, rpm_limit, tpm_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            new_id = LLMModel.execute_query(
                query,
                (name, provider, model_id, api_key, is_active, rpm_limit, tpm_limit),
                commit=True
            )
            logger.info(f"[LLMModel] Created connection ID: {new_id}")
            return LLMModel.get_connection(new_id, include_secret=False)
        except Exception as e:
            logger.error(f"[LLMModel] create_connection error: {e}")
            return None

    @staticmethod
    def get_connection(conn_id, include_secret=True):
        try:
            query = "SELECT * FROM llm_connections WHERE id = ?"
            row = LLMModel.execute_query(query, (conn_id,), fetch_one=True)
            if row: return LLMModel._format_connection(row, include_secret=include_secret)
            return None
        except Exception as e:
            logger.error(f"[LLMModel] get_connection error: {e}")
            return None

    @staticmethod
    def list_connections():
        try:
            query = "SELECT * FROM llm_connections ORDER BY id ASC"
            rows = LLMModel.execute_query(query)
            return [LLMModel._format_connection(r, include_secret=False) for r in rows] if rows else []
        except Exception as e:
            logger.error(f"[LLMModel] list_connections error: {e}")
            return []

    @staticmethod
    def update_connection(conn_id, data=None, **kwargs):
        try:
            existing = LLMModel.get_connection(conn_id)
            if not existing:
                logger.warning(f"[LLMModel] Connection {conn_id} not found for update.")
                return False

            if data is None:
                data = {}
            if not isinstance(data, dict):
                logger.error("[LLMModel] update_connection data must be dict")
                return False
            if kwargs:
                data = {**data, **kwargs}
            
            allowed_fields = ['name', 'provider', 'model_id', 'api_key',
                              'is_active', 'verify_status', 'verified_capability', 'verified_at', 'last_error',
                              'rpm_limit', 'tpm_limit', 'available_models']
            set_clauses = []
            values = []
            
            for field in allowed_fields:
                if field in data:
                    val = data[field]
                    if field == 'api_key' and val == '******': continue
                    set_clauses.append(f"{field} = ?")
                    values.append(val)
                    
            if not set_clauses: return True 
            
            values.append(conn_id)
            query = f"UPDATE llm_connections SET {', '.join(set_clauses)} WHERE id = ?"
            LLMModel.execute_query(query, tuple(values), commit=True)
            logger.info(f"[LLMModel] Updated connection {conn_id}")
            return True
        except Exception as e:
            logger.error(f"[LLMModel] update_connection error: {e}")
            return False

    @staticmethod
    def delete_connection(conn_id):
        try:
            LLMModel.execute_query("UPDATE task_bindings SET connection_id = NULL, is_locked = 0 WHERE connection_id = ?", (conn_id,), commit=True)
            LLMModel.execute_query("DELETE FROM llm_connections WHERE id = ?", (conn_id,), commit=True)
            logger.info(f"[LLMModel] Deleted connection {conn_id}")
            return True
        except Exception as e:
            logger.error(f"[LLMModel] delete_connection error: {e}")
            return False

    @staticmethod
    def _format_connection(row, include_secret=True):
        if not row: return None
        d = dict(row) if not isinstance(row, dict) else row
        api_key_raw = d.get('api_key', '') or ''
        api_key_masked = (api_key_raw[:4] + '****') if len(api_key_raw) > 4 else ('****' if api_key_raw else '')
        return {
            'id': d.get('id'),
            'name': d.get('name', ''),
            'provider': d.get('provider', ''),
            'model_id': d.get('model_id', ''),
            'api_key_enc': api_key_masked,
            'api_key': api_key_raw if include_secret else api_key_masked,
            'available_models': d.get('available_models', ''),
            'is_active': bool(d.get('is_active', 0)),
            'verify_status': d.get('verify_status', 'unverified') or 'unverified',
            'verified_capability': d.get('verified_capability', '') or '',
            'verified_at': d.get('verified_at'),
            'last_error': d.get('last_error', '') or '',
            'rpm_limit': d.get('rpm_limit', 60),
            'tpm_limit': d.get('tpm_limit', 1000000),
            'has_key': bool(api_key_raw),
            'created_at': d.get('created_at', '')
        }

    # =========================================================
    # 3. Binding CRUD (task_bindings 表)
    # =========================================================

    @staticmethod
    def get_binding_for_task(task_id):
        try:
            query = "SELECT * FROM task_bindings WHERE task_id = ?"
            row = LLMModel.execute_query(query, (task_id,), fetch_one=True)
            if row: return LLMModel._format_binding(row)
            return None
        except Exception as e:
            logger.error(f"[LLMModel] get_binding_for_task error: {e}")
            return None

    @staticmethod
    def list_bindings():
        try:
            query = "SELECT * FROM task_bindings ORDER BY task_id"
            rows = LLMModel.execute_query(query)
            return [LLMModel._format_binding(r) for r in rows] if rows else []
        except Exception as e:
            logger.error(f"[LLMModel] list_bindings error: {e}")
            return []

    @staticmethod
    def list_bindings_full():
        try:
            query = "SELECT * FROM task_bindings ORDER BY task_id"
            rows = LLMModel.execute_query(query)
            return [LLMModel._format_binding(r) for r in rows] if rows else []
        except Exception as e:
            logger.error(f"[LLMModel] list_bindings_full error: {e}")
            return []

    @staticmethod
    def update_binding(task_id, connection_id):
        try:
            if connection_id is None or connection_id == '' or connection_id == 'null':
                LLMModel.execute_query("UPDATE task_bindings SET connection_id = NULL, is_locked = 0 WHERE task_id = ?", (task_id,), commit=True)
                logger.info(f"[LLMModel] Unbound task: {task_id}")
            else:
                conn_id = int(connection_id)
                LLMModel.execute_query(
                    """INSERT INTO task_bindings (task_id, connection_id, is_locked) 
                       VALUES (?, ?, 0)
                       ON CONFLICT(task_id) DO UPDATE SET connection_id = ?, is_locked = 0""",
                    (task_id, conn_id, conn_id), commit=True
                )
                logger.info(f"[LLMModel] Bound task {task_id} → connection {conn_id}")
            return True
        except Exception as e:
            logger.error(f"[LLMModel] update_binding error: {e}")
            return False

    @staticmethod
    def delete_binding(task_id):
        return LLMModel.update_binding(task_id, None)

    @staticmethod
    def _format_binding(row):
        if not row: return None
        d = dict(row) if not isinstance(row, dict) else row
        return {
            'task_id': d.get('task_id'),
            'connection_id': d.get('connection_id'),
            'bus_id': d.get('connection_id'), 
            'is_locked': bool(d.get('is_locked', 0))
        }

    # =========================================================
    # 4. 複合查詢 & 通用查詢執行器
    # =========================================================

    @staticmethod
    def get_task_definition(task_id):
        task_key = str(task_id or "").strip()
        for task_def in RAG_TASKS:
            if task_def.get("task_id") == task_key:
                return dict(task_def)
        return None

    @staticmethod
    def get_task_capability(task_id):
        task_def = LLMModel.get_task_definition(task_id)
        if not task_def:
            return None
        return task_def.get("capability")

    @staticmethod
    def get_connection_for_task(task_id):
        try:
            query = """
                SELECT c.* FROM llm_connections c
                INNER JOIN task_bindings b ON c.id = b.connection_id
                WHERE b.task_id = ?
                  AND c.is_active = 1
                  AND COALESCE(c.verify_status, 'unverified') = 'ok'
            """
            row = LLMModel.execute_query(query, (task_id,), fetch_one=True)
            if row:
                formatted = LLMModel._format_connection(row, include_secret=True)
                required_capability = LLMModel.get_task_capability(task_id)
                if required_capability and not LLMModel.is_connection_ready(formatted, required_capability):
                    logger.warning(
                        "[LLMModel] Bound connection is not verified for task capability: task=%s capability=%s",
                        task_id,
                        required_capability
                    )
                    return None
                return formatted
            return None
        except Exception as e:
            logger.error(f"[LLMModel] get_connection_for_task error: {e}")
            return None

    @staticmethod
    def is_connection_ready(connection, required_capability=None):
        if not connection:
            return False
        conn = dict(connection)
        base_ready = bool(
            conn.get('is_active')
            and conn.get('verify_status') == 'ok'
            and conn.get('model_id')
            and conn.get('has_key')
        )
        if not base_ready:
            return False
        if required_capability:
            return conn.get('verified_capability') == required_capability
        return bool(conn.get('verified_capability'))

    @staticmethod
    def execute_query(query, args=(), fetch_one=False, commit=False):
        path = LLMModel.get_db_path()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row 
        c = conn.cursor()
        try:
            c.execute(query, args)
            if commit:
                conn.commit()
                last_id = c.lastrowid
                conn.close()
                return last_id
            res = c.fetchone() if fetch_one else c.fetchall()
            conn.close()
            
            if res is None: return None
            if fetch_one: return dict(res)
            return [dict(r) for r in res]
        except Exception as e:
            conn.close()
            raise e

    # =========================================================
    # [New in v0.3/v0.4] OpenRouter & Provider Diagnostic Tools
    # =========================================================
    @staticmethod
    def validate_provider_format(provider: str, model_id: str) -> bool:
        """
        診斷模型供應商的合法性，確保 OpenRouter 等複雜 ID 格式正確
        """
        if not provider or not model_id:
            return False
        if provider.lower() == 'openrouter':
            # OpenRouter model_id 通常包含 '/' (例如: 'anthropic/claude-3.5-sonnet')
            return '/' in model_id or len(model_id) > 2
        return True
