# 檔案路徑: rootmedicals-a/llmxx-server/server_app/infra/state_db.py
# 產生時間: 2026-06-18 11:38 +08:00
# 版本: v0.2
# 模組定位:
#   llmxx-server 的 SQLite runtime state store。它保存 session 狀態、event log、RAG request/response
#   與 doctor viewer 需要讀取的最新結果。
# 主要責任:
#   1. 建立 clinical_sessions / clinical_events schema。
#   2. 保存 intake 到 final gate 的狀態轉換。
#   3. 提供 /demo/latest、polling 與工程診斷需要的事件序列。
# 維護提醒:
#   - response_json 可以保存燈號與摘要，但不要在這裡新增原始截圖或未遮蔽 PHI。
#   - SQLite WAL 可降低 dashboard/polling 與寫入互卡，但仍不是高併發正式資料庫。
# 驗證方式:
#   - /api/health online 後 data/llmxx_server.db 應存在。
#   - 一次 /api/intake 應新增 clinical_sessions 與多筆 clinical_events。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .settings import ensure_runtime_dirs, settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    # DB 欄位用 JSON text 保存可變結構；None 統一成 {}，方便 viewer 端解析。
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


class StateDB:
    def __init__(self, db_path: Path | None = None):
        ensure_runtime_dirs()
        self.db_path = Path(db_path or settings.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        # WAL 讓讀取 latest/demo viewer 時不容易被寫入鎖住；busy_timeout 避免短暫鎖定直接失敗。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            # clinical_sessions 是每次 Ctrl+Alt+G 或 API intake 的主紀錄；clinical_events 則保存流程階段。
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clinical_sessions (
                    id TEXT PRIMARY KEY,
                    client_session_id TEXT,
                    correlation_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    payload_schema_version TEXT NOT NULL,
                    source TEXT,
                    input_origin TEXT,
                    zero_disk_image_io INTEGER DEFAULT 1,
                    patient_uid_ref TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    degraded_reason TEXT,
                    dx TEXT,
                    tx TEXT,
                    hx TEXT,
                    parse_confidence REAL DEFAULT 0,
                    rag_request_json TEXT DEFAULT '{}',
                    rag_response_json TEXT DEFAULT '{}',
                    rag_query_id TEXT,
                    light_color TEXT,
                    final_score REAL DEFAULT 0,
                    error TEXT,
                    response_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clinical_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    error_code TEXT,
                    event_metadata_json TEXT DEFAULT '{}',
                    UNIQUE(session_id, seq),
                    FOREIGN KEY(session_id) REFERENCES clinical_sessions(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_client_payload ON clinical_sessions(client_session_id, payload_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON clinical_sessions(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON clinical_sessions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session_seq ON clinical_events(session_id, seq)")

    def find_by_client_session(self, client_session_id: str) -> list[sqlite3.Row]:
        if not client_session_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clinical_sessions WHERE client_session_id = ? ORDER BY created_at DESC",
                (client_session_id,),
            ).fetchall()
            return rows

    def create_session(
        self,
        *,
        client_session_id: str,
        payload_hash_value: str,
        schema_version: str,
        source: str,
        input_origin: str,
        zero_disk_image_io: bool,
        patient_uid_ref_value: str | None,
    ) -> dict[str, Any]:
        now = utc_now()
        session_id = f"server-{uuid.uuid4().hex[:16]}"
        correlation_id = f"corr-{uuid.uuid4().hex[:16]}"
        # session_id 是 server 內部主鍵；client_session_id 用於醫師端 polling，兩者刻意分開。
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO clinical_sessions (
                    id, client_session_id, correlation_id, payload_hash, created_at, updated_at,
                    payload_schema_version, source, input_origin, zero_disk_image_io,
                    patient_uid_ref, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    client_session_id,
                    correlation_id,
                    payload_hash_value,
                    now,
                    now,
                    schema_version,
                    source,
                    input_origin,
                    1 if zero_disk_image_io else 0,
                    patient_uid_ref_value,
                    "received",
                ),
            )
            self._insert_event_tx(conn, session_id, "intake", "info", "received", "Formal payload accepted.", None, {"has_client_session_id": bool(client_session_id)})
            row = conn.execute("SELECT * FROM clinical_sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row)

    def _next_seq_tx(self, conn: sqlite3.Connection, session_id: str) -> int:
        # event seq 以 session 為範圍遞增，doctor viewer 才能照流程順序顯示 intake/parse/RAG/gate。
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM clinical_events WHERE session_id = ?", (session_id,)).fetchone()
        return int(row["next_seq"])

    def _insert_event_tx(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        stage: str,
        level: str,
        event_type: str,
        message: str,
        error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        seq = self._next_seq_tx(conn, session_id)
        conn.execute(
            """
            INSERT INTO clinical_events (session_id, seq, created_at, stage, level, event_type, message, error_code, event_metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, seq, utc_now(), stage, level, event_type, message, error_code, _json_dumps(metadata or {})),
        )

    def add_event(self, session_id: str, stage: str, level: str, event_type: str, message: str, error_code: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            self._insert_event_tx(conn, session_id, stage, level, event_type, message, error_code, metadata)

    def update_session(self, session_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status", "completed_at", "error_code", "degraded_reason", "dx", "tx", "hx",
            "parse_confidence", "rag_request_json", "rag_response_json", "rag_query_id",
            "light_color", "final_score", "error", "response_json",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        updates["updated_at"] = utc_now()
        if not updates:
            return self.get_session(session_id) or {}
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [session_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE clinical_sessions SET {assignments} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM clinical_sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else {}

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM clinical_sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    def latest_by_client_session(self, client_session_id: str) -> dict[str, Any] | None:
        if not client_session_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM clinical_sessions WHERE client_session_id = ? ORDER BY created_at DESC LIMIT 1",
                (client_session_id,),
            ).fetchone()
            return dict(row) if row else None

    def latest_session(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM clinical_sessions ORDER BY created_at DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clinical_events WHERE session_id = ? ORDER BY seq ASC",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]


db = StateDB()

