# 模組定位: llmebm 動態 Topic Page 的版本化持久層。
# 主要責任: 保存 DOM manifest、逐 slot 內容版本/結構與 evidence revision、生成工作狀態。
# 呼叫來源: main_ebm.py 的 Topic Page manifest/content/status/generate 路由與掃描工具。
# 輸入契約: 已驗證的 llmebm-topic-manifest.v1、llmebm-topic-content.v1 與結構化失敗資料。
# 輸出契約: SQLite 中可追溯、單一 current 的內容版本及 empty/ready/stale/failed 狀態摘要。
# 安全邊界: 截圖只可從 allowlisted scan root 讀取；內容只接受 allowlisted component JSON。
# 維護提醒: 結構/evidence 正常路徑逐 slot 比對；缺 legacy metadata 時才接受整頁 fail-safe。
# ----------------------------------------------------------------------------------------------------

import hashlib
import base64
import json
import os
import sqlite3
import uuid
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_DB_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "data", "system"))
os.makedirs(SYSTEM_DB_DIR, exist_ok=True)
TOPIC_CONTENT_DB_PATH = os.getenv("LLMEBM_TOPIC_CONTENT_DB_PATH", os.path.join(SYSTEM_DB_DIR, "topic_content.db"))
TOPIC_SCAN_ROOT = os.path.abspath(os.getenv(
    "LLMEBM_TOPIC_SCAN_ROOT", os.path.join(BASE_DIR, "..", "..", "data", "runtime", "topic_scans")
))

SCHEMA_MANIFEST = "llmebm-topic-manifest.v1"
SCHEMA_CONTENT = "llmebm-topic-content.v1"
ALLOWED_BLOCKS = ("summary", "recommendations", "bullets", "evidence_note", "table", "warning")
CURRENTABLE_STATUSES = {"ready", "insufficient_evidence"}
WORKFLOW_STATUSES = {"generated", "review_pending", "approved", "published", "rejected", "retired"}
AUTOMATION_JOB_TYPES = {"scan", "regenerate"}
AUTOMATION_JOB_STATUSES = {"pending", "running", "waiting_review", "completed", "failed", "superseded"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _workflow_text(value, field, max_length):
    text = " ".join(str(value or "").split()).strip()
    if not text or len(text) > max_length:
        raise ValueError(f"{field} must be 1..{max_length} characters.")
    return text


def validate_evidence_revision(value):
    if value is None:
        return None
    digest = value.removeprefix("sha256:") if isinstance(value, str) else ""
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("evidence_revision must be a sha256 digest.")
    return value


def validate_evidence_revisions(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("evidence_revisions must be an object keyed by slot_id.")
    return {
        str(slot_id): validate_evidence_revision(revision)
        for slot_id, revision in value.items()
    }


def canonical_manifest_fields(manifest):
    """Keep only fields that change the semantic slot contract."""
    slots = []
    for slot in manifest.get("slots", []):
        slots.append({
            "slot_id": str(slot["slot_id"]),
            "heading": str(slot["heading"]),
            "heading_path": [str(part) for part in slot.get("heading_path", [])],
            "level": int(slot["level"]),
            "order": int(slot["order"]),
            "content_target": bool(slot.get("content_target", True)),
            "allowed_blocks": [str(value) for value in slot.get("allowed_blocks", ALLOWED_BLOCKS)],
        })
    return {
        "schema": SCHEMA_MANIFEST,
        "topic_uid": str(manifest["topic_uid"]),
        "topic_name": str(manifest["topic_name"]),
        "template_version": str(manifest.get("template_version", "condition.v1")),
        "slots": slots,
    }


def manifest_hash(manifest):
    payload = json.dumps(
        canonical_manifest_fields(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def slot_structure_revision(manifest, slot):
    """Fingerprint only the fields that can change one slot's generated clinical-content contract."""
    if not isinstance(manifest, dict) or not isinstance(slot, dict):
        return None
    heading_path = slot.get("heading_path")
    allowed_blocks = slot.get("allowed_blocks", ALLOWED_BLOCKS)
    if (
        not isinstance(heading_path, (list, tuple))
        or not isinstance(allowed_blocks, (list, tuple))
        or not {"slot_id", "heading", "level"}.issubset(slot)
    ):
        return None
    try:
        payload = {
            "schema": SCHEMA_MANIFEST,
            "template_version": str(manifest.get("template_version", "condition.v1")),
            "slot_id": str(slot["slot_id"]),
            "heading": str(slot["heading"]),
            "heading_path": [str(part) for part in heading_path],
            "level": int(slot["level"]),
            "content_target": bool(slot.get("content_target", True)),
            "allowed_blocks": sorted(str(value) for value in allowed_blocks),
        }
    except (TypeError, ValueError):
        return None
    # ponytail: sibling order is layout-only today; include it if a future composer uses adjacency as evidence context.
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def manifest_structure_revisions(manifest):
    if not isinstance(manifest, dict):
        return {}
    revisions = {}
    for slot in manifest.get("slots", []):
        if not isinstance(slot, dict):
            continue
        revision = slot_structure_revision(manifest, slot)
        if revision:
            revisions[str(slot["slot_id"])] = revision
    return revisions


def affected_manifest_slot_ids(previous_manifest, current_manifest):
    """Return only new or structurally changed content targets, in current DOM order."""
    previous = manifest_structure_revisions(previous_manifest)
    current = manifest_structure_revisions(current_manifest)
    return [
        str(slot["slot_id"])
        for slot in (current_manifest or {}).get("slots", [])
        if slot.get("content_target", True)
        and current.get(str(slot.get("slot_id"))) != previous.get(str(slot.get("slot_id")))
    ]


def build_manifest(topic_name, topic_uid, tree, viewport=None, screenshots=None):
    slots = []

    def walk(nodes, parent_path=()):
        for node in nodes:
            heading_path = [*parent_path, node["name"]]
            allowed_blocks = node.get("allowed_blocks", ALLOWED_BLOCKS)
            if (
                not isinstance(allowed_blocks, (list, tuple))
                or not allowed_blocks
                or any(block not in ALLOWED_BLOCKS for block in allowed_blocks)
            ):
                raise ValueError(f"Invalid allowed_blocks for slot {node.get('slot_id', 'unknown')}.")
            slots.append({
                "slot_id": node["slot_id"],
                "heading": node["name"],
                "heading_path": heading_path,
                "level": node["layer"],
                "order": len(slots),
                "content_target": bool(node.get("content_target", True)),
                "allowed_blocks": list(dict.fromkeys(allowed_blocks)),
            })
            walk(node.get("children", []), heading_path)

    walk(tree.get("universal", []))
    walk(tree.get("custom", []))
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "topic_uid": topic_uid,
        "topic_name": topic_name,
        "template_version": "condition.v1",
        "viewport": viewport or {"width": 1440, "height": 1000},
        "screenshots": screenshots or [],
        "slots": slots,
    }
    manifest["dom_hash"] = manifest_hash(manifest)
    return manifest


def validate_content(content, expected_slot_id=None):
    if not isinstance(content, dict) or content.get("schema") != SCHEMA_CONTENT:
        raise ValueError("Invalid topic content schema.")
    slot_id = content.get("slot_id")
    if not isinstance(slot_id, str) or not slot_id or (expected_slot_id and slot_id != expected_slot_id):
        raise ValueError("Topic content slot_id does not match the requested slot.")
    if content.get("status") not in CURRENTABLE_STATUSES:
        raise ValueError("Only ready or insufficient_evidence content can become current.")
    blocks = content.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("Topic content blocks must be a list.")
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in ALLOWED_BLOCKS:
            raise ValueError("Topic content contains an unsupported block type.")
        block_type = block["type"]
        if block_type in {"summary", "recommendations", "evidence_note", "warning"}:
            if not isinstance(block.get("text"), str) or not isinstance(block.get("citations"), list):
                raise ValueError(f"Topic content {block_type} block is malformed.")
            if block_type == "recommendations":
                for field in ("recommendation_strength", "evidence_certainty"):
                    value = block.get(field)
                    if value is not None and (not isinstance(value, str) or not value or len(value) > 80):
                        raise ValueError(f"Topic content {field} must be a short evidence-sourced label.")
        elif block_type == "bullets":
            items = block.get("items")
            if not isinstance(items, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("text"), str)
                or not isinstance(item.get("citations"), list) for item in items
            ):
                raise ValueError("Topic content bullets block is malformed.")
        elif block_type == "table":
            columns, rows = block.get("columns"), block.get("rows")
            if not isinstance(columns, list) or not columns or not isinstance(rows, list):
                raise ValueError("Topic content table block is malformed.")
            if any(
                not isinstance(row, dict) or not isinstance(row.get("cells"), list)
                or len(row["cells"]) != len(columns) or not isinstance(row.get("citations"), list)
                for row in rows
            ):
                raise ValueError("Topic content table rows are malformed.")
    return content


def content_paper_ids(content):
    """Return unique citation paper IDs from the validated component tree."""
    valid = validate_content(content)
    paper_ids = []

    def add(citations):
        for citation in citations or []:
            paper_id = str((citation or {}).get("paper_id") or "").strip() if isinstance(citation, dict) else ""
            if paper_id and paper_id not in paper_ids:
                paper_ids.append(paper_id)

    for block in valid["blocks"]:
        if block["type"] == "bullets":
            for item in block["items"]:
                add(item.get("citations"))
        elif block["type"] == "table":
            for row in block["rows"]:
                add(row.get("citations"))
        else:
            add(block.get("citations"))
    return paper_ids


def load_screenshot_payloads(manifest, artifact_root=TOPIC_SCAN_ROOT):
    """Load verified local scan artifacts without exposing their paths to ebm-rag."""
    screenshots = manifest.get("screenshots") or []
    if not 1 <= len(screenshots) <= 3:
        raise ValueError("scan_required: current manifest must reference 1 to 3 screenshots.")
    root = Path(artifact_root).resolve()
    payloads = []
    for screenshot in screenshots:
        artifact_path = screenshot.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            raise ValueError("scan_required: screenshot artifact_path is missing.")
        candidate = (root / artifact_path).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("scan_required: screenshot artifact escaped the allowlisted root.")
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise ValueError("scan_required: screenshot artifact is unavailable.") from exc
        if len(data) > 5 * 1024 * 1024:
            raise ValueError("scan_required: screenshot artifact exceeds 5 MiB.")
        declared_mime = screenshot.get("mime_type")
        actual_mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else (
            "image/jpeg" if data.startswith(b"\xff\xd8\xff") else None
        )
        if actual_mime is None or declared_mime != actual_mime:
            raise ValueError("scan_required: screenshot artifact MIME type is invalid.")
        actual_sha = hashlib.sha256(data).hexdigest()
        if screenshot.get("sha256") != actual_sha:
            raise ValueError("scan_required: screenshot artifact hash mismatch.")
        payloads.append({
            "mime_type": actual_mime,
            "image_b64": base64.b64encode(data).decode("ascii"),
            "state": str(screenshot.get("state", "topic")),
        })
    return payloads


class TopicContentDatabase:
    def __init__(self, db_path=TOPIC_CONTENT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()
        if os.getenv("LLMEBM_INTERRUPT_GENERATION_JOBS_ON_START", "1") == "1":
            self.interrupt_running_jobs()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS topic_manifests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_uid TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    template_version TEXT NOT NULL,
                    dom_hash TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    screenshot_sha256_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(topic_uid, dom_hash)
                );
                CREATE TABLE IF NOT EXISTS topic_content_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_uid TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT,
                    content_json TEXT,
                    query_id TEXT,
                    evidence_digest TEXT,
                    evidence_revision TEXT,
                    structure_revision TEXT,
                    model_json TEXT,
                    error_json TEXT,
                    workflow_status TEXT NOT NULL DEFAULT 'generated',
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    published_at TEXT,
                    is_current INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS topic_generation_jobs (
                    job_id TEXT PRIMARY KEY,
                    topic_uid TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    requested_slot_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS topic_automation_jobs (
                    job_id TEXT PRIMARY KEY,
                    topic_uid TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    job_type TEXT NOT NULL CHECK(job_type IN ('scan', 'regenerate')),
                    slot_id TEXT,
                    dedupe_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    worker_id TEXT,
                    result_json TEXT,
                    error_json TEXT,
                    available_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS topic_content_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_uid TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    content_version_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    comment TEXT,
                    before_status TEXT NOT NULL,
                    after_status TEXT NOT NULL,
                    source_gate_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(content_version_id) REFERENCES topic_content_versions(id)
                );
                CREATE TABLE IF NOT EXISTS topic_user_state (
                    topic_uid TEXT PRIMARY KEY,
                    followed INTEGER NOT NULL DEFAULT 0 CHECK(followed IN (0, 1)),
                    last_seen_at TEXT,
                    last_seen_status_hash TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_manifest_current
                    ON topic_manifests(topic_uid, id DESC);
                CREATE INDEX IF NOT EXISTS idx_content_current
                    ON topic_content_versions(topic_uid, slot_id, is_current);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_current_content
                    ON topic_content_versions(topic_uid, slot_id) WHERE is_current = 1;
                CREATE INDEX IF NOT EXISTS idx_topic_jobs
                    ON topic_generation_jobs(topic_uid, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_automation_claim
                    ON topic_automation_jobs(status, available_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_automation_topic
                    ON topic_automation_jobs(topic_uid, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_automation_job
                    ON topic_automation_jobs(dedupe_key)
                    WHERE status IN ('pending', 'running', 'waiting_review');
                CREATE INDEX IF NOT EXISTS idx_topic_reviews
                    ON topic_content_reviews(topic_uid, slot_id, created_at DESC);
            """)
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(topic_content_versions)").fetchall()
            }
            if "evidence_revision" not in columns:
                conn.execute("ALTER TABLE topic_content_versions ADD COLUMN evidence_revision TEXT")
            if "structure_revision" not in columns:
                conn.execute("ALTER TABLE topic_content_versions ADD COLUMN structure_revision TEXT")
            if "workflow_status" not in columns:
                conn.execute("ALTER TABLE topic_content_versions ADD COLUMN workflow_status TEXT NOT NULL DEFAULT 'generated'")
            if "reviewed_by" not in columns:
                conn.execute("ALTER TABLE topic_content_versions ADD COLUMN reviewed_by TEXT")
            if "reviewed_at" not in columns:
                conn.execute("ALTER TABLE topic_content_versions ADD COLUMN reviewed_at TEXT")
            if "published_at" not in columns:
                conn.execute("ALTER TABLE topic_content_versions ADD COLUMN published_at TEXT")
            user_state_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(topic_user_state)").fetchall()
            }
            if "last_seen_status_hash" not in user_state_columns:
                conn.execute("ALTER TABLE topic_user_state ADD COLUMN last_seen_status_hash TEXT")
            self._backfill_structure_revisions(conn)
            conn.commit()

    @staticmethod
    def _backfill_structure_revisions(conn):
        """Idempotently attach legacy content rows to the exact slot contract of their source manifest."""
        manifests = conn.execute(
            "SELECT topic_uid, dom_hash, manifest_json FROM topic_manifests"
        ).fetchall()
        for row in manifests:
            try:
                manifest = json.loads(row["manifest_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            for slot_id, revision in manifest_structure_revisions(manifest).items():
                conn.execute(
                    """UPDATE topic_content_versions SET structure_revision = ?
                       WHERE topic_uid = ? AND manifest_hash = ? AND slot_id = ?
                         AND structure_revision IS NULL""",
                    (revision, row["topic_uid"], row["dom_hash"], slot_id),
                )

    @staticmethod
    def _save_manifest_in_connection(conn, manifest):
        canonical = canonical_manifest_fields(manifest)
        dom_hash = manifest_hash(manifest)
        stored = {**manifest, "dom_hash": dom_hash}
        screenshots = [shot.get("sha256", "") for shot in stored.get("screenshots", [])]
        conn.execute(
            "UPDATE topic_manifests SET status = 'superseded' WHERE topic_uid = ? AND status = 'current'",
            (canonical["topic_uid"],),
        )
        conn.execute(
            """INSERT INTO topic_manifests
               (topic_uid, topic_name, template_version, dom_hash, manifest_json,
                screenshot_sha256_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'current', ?)
               ON CONFLICT(topic_uid, dom_hash) DO UPDATE SET
                   topic_name = excluded.topic_name,
                   template_version = excluded.template_version,
                   manifest_json = excluded.manifest_json,
                   screenshot_sha256_json = excluded.screenshot_sha256_json,
                   status = 'current'""",
            (
                canonical["topic_uid"], canonical["topic_name"], canonical["template_version"], dom_hash,
                json.dumps(stored, ensure_ascii=False), json.dumps(screenshots), _now(),
            ),
        )
        return stored

    @staticmethod
    def _enqueue_automation_in_connection(
        conn, *, topic_uid, topic_name, job_type, dedupe_key, payload, max_attempts=3,
        reuse_terminal=False,
    ):
        if job_type not in AUTOMATION_JOB_TYPES:
            raise ValueError("Unsupported automation job type.")
        if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False)) > 100_000:
            raise ValueError("Automation payload must be a bounded object.")
        if reuse_terminal:
            existing = conn.execute(
                """SELECT job_id FROM topic_automation_jobs
                   WHERE dedupe_key = ? ORDER BY created_at DESC LIMIT 1""",
                (dedupe_key,),
            ).fetchone()
            if existing:
                return existing["job_id"]
        job_id = str(uuid.uuid4())
        now = _now()
        cursor = conn.execute(
            """INSERT OR IGNORE INTO topic_automation_jobs
               (job_id, topic_uid, topic_name, job_type, slot_id, dedupe_key, payload_json,
                status, attempt_count, max_attempts, available_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)""",
            (
                job_id, topic_uid, topic_name, job_type, payload.get("slot_id"), dedupe_key,
                json.dumps(payload, ensure_ascii=False), max(1, int(max_attempts)), now, now, now,
            ),
        )
        if cursor.rowcount:
            return job_id
        row = conn.execute(
            """SELECT job_id FROM topic_automation_jobs
               WHERE dedupe_key = ? AND status IN ('pending', 'running', 'waiting_review')
               ORDER BY created_at DESC LIMIT 1""",
            (dedupe_key,),
        ).fetchone()
        if not row:
            raise RuntimeError("Automation queue dedupe conflict could not be resolved.")
        return row["job_id"]

    def save_manifest(self, manifest):
        with self.get_connection() as conn:
            stored = self._save_manifest_in_connection(conn, manifest)
            conn.commit()
        return stored

    def save_manifest_and_enqueue_scan(self, manifest, affected_slot_ids):
        """Atomically persist a dirty structure contract and its required DOM+screenshot scan."""
        affected = list(dict.fromkeys(str(slot_id) for slot_id in affected_slot_ids if str(slot_id)))
        with self.get_connection() as conn:
            stored = self._save_manifest_in_connection(conn, manifest)
            job_id = None
            if affected:
                job_id = self._enqueue_automation_in_connection(
                    conn,
                    topic_uid=stored["topic_uid"],
                    topic_name=stored["topic_name"],
                    job_type="scan",
                    dedupe_key=f"scan:{stored['topic_uid']}:{stored['dom_hash']}",
                    payload={
                        "expected_manifest_hash": stored["dom_hash"],
                        "affected_slot_ids": affected,
                    },
                )
            conn.commit()
        return stored, job_id

    def enqueue_automation_job(
        self, *, topic_uid, topic_name, job_type, dedupe_key, payload, max_attempts=3,
        reuse_terminal=False,
    ):
        with self.get_connection() as conn:
            job_id = self._enqueue_automation_in_connection(
                conn,
                topic_uid=topic_uid,
                topic_name=topic_name,
                job_type=job_type,
                dedupe_key=dedupe_key,
                payload=payload,
                max_attempts=max_attempts,
                reuse_terminal=reuse_terminal,
            )
            conn.commit()
        return job_id

    @staticmethod
    def _automation_row(row):
        if not row:
            return None
        result = dict(row)
        for column, key in (("payload_json", "payload"), ("result_json", "result"), ("error_json", "error")):
            raw = result.pop(column, None)
            result[key] = json.loads(raw) if raw else None
        return result

    def claim_automation_job(self, worker_id):
        worker_id = _workflow_text(worker_id, "worker_id", 120)
        now = _now()
        with self.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM topic_automation_jobs
                   WHERE status = 'pending' AND available_at <= ? AND attempt_count < max_attempts
                   ORDER BY created_at, job_id LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            updated = conn.execute(
                """UPDATE topic_automation_jobs
                   SET status = 'running', worker_id = ?, attempt_count = attempt_count + 1, updated_at = ?
                   WHERE job_id = ? AND status = 'pending'""",
                (worker_id, now, row["job_id"]),
            )
            if updated.rowcount != 1:
                conn.rollback()
                return None
            claimed = conn.execute(
                "SELECT * FROM topic_automation_jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            conn.commit()
        return self._automation_row(claimed)

    def update_automation_job(self, job_id, status, *, result=None, error=None):
        if status not in AUTOMATION_JOB_STATUSES:
            raise ValueError("Unsupported automation job status.")
        with self.get_connection() as conn:
            conn.execute(
                """UPDATE topic_automation_jobs
                   SET status = ?, worker_id = CASE WHEN ? = 'running' THEN worker_id ELSE NULL END,
                       result_json = ?, error_json = ?, updated_at = ? WHERE job_id = ?""",
                (
                    status, status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    _now(), job_id,
                ),
            )
            conn.commit()

    def get_automation_job(self, job_id):
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM topic_automation_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._automation_row(row)

    def automation_status(self, topic_uid, limit=100):
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM topic_automation_jobs WHERE topic_uid = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (topic_uid, max(1, min(int(limit), 200))),
            ).fetchall()
        jobs = [self._automation_row(row) for row in rows]
        counts = {}
        for job in jobs:
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        return {"counts": counts, "jobs": jobs}

    def recover_running_automation_jobs(self):
        """Requeue work abandoned by a stopped worker; active-job dedupe remains intact."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """UPDATE topic_automation_jobs
                   SET status = 'pending', worker_id = NULL, available_at = ?, updated_at = ?
                   WHERE status = 'running'""",
                (_now(), _now()),
            )
            conn.commit()
        return cursor.rowcount

    def retry_automation_job(self, job_id, error, delay_seconds=15):
        """Requeue a transient failure until its persisted attempt ceiling is reached."""
        now = datetime.now(timezone.utc)
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT attempt_count, max_attempts FROM topic_automation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown automation job.")
            status = "pending" if row["attempt_count"] < row["max_attempts"] else "failed"
            available_at = (now + timedelta(seconds=max(0, float(delay_seconds)))).isoformat()
            conn.execute(
                """UPDATE topic_automation_jobs
                   SET status = ?, worker_id = NULL, error_json = ?, available_at = ?, updated_at = ?
                   WHERE job_id = ?""",
                (status, json.dumps(error, ensure_ascii=False), available_at, now.isoformat(), job_id),
            )
            conn.commit()
        return status

    def resume_waiting_regeneration(self, topic_uid, slot_id):
        with self.get_connection() as conn:
            cursor = conn.execute(
                """UPDATE topic_automation_jobs
                   SET status = 'pending', worker_id = NULL, attempt_count = 0, available_at = ?, updated_at = ?
                   WHERE topic_uid = ? AND slot_id = ? AND job_type = 'regenerate'
                     AND status = 'waiting_review'""",
                (_now(), _now(), topic_uid, slot_id),
            )
            conn.commit()
        return cursor.rowcount

    def get_current_manifest(self, topic_name):
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT manifest_json FROM topic_manifests
                   WHERE topic_name = ? AND status = 'current' ORDER BY id DESC LIMIT 1""",
                (topic_name,),
            ).fetchone()
        return json.loads(row["manifest_json"]) if row else None

    def list_current_manifests(self):
        """Return each llmebm-owned current topic contract for bounded worker reconciliation."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT manifest_json FROM topic_manifests
                   WHERE status = 'current' ORDER BY topic_name, id"""
            ).fetchall()
        return [json.loads(row["manifest_json"]) for row in rows]

    def create_job(self, topic_uid, manifest_hash_value, slot_ids):
        job_id = str(uuid.uuid4())
        now = _now()
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO topic_generation_jobs
                   (job_id, topic_uid, manifest_hash, requested_slot_ids_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (job_id, topic_uid, manifest_hash_value, json.dumps(slot_ids), now, now),
            )
            conn.commit()
        return job_id

    def update_job(self, job_id, status, error=None):
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE topic_generation_jobs SET status = ?, error_json = ?, updated_at = ? WHERE job_id = ?",
                (status, json.dumps(error, ensure_ascii=False) if error else None, _now(), job_id),
            )
            conn.commit()

    def get_generation_job(self, job_id):
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT job_id, topic_uid, manifest_hash, requested_slot_ids_json,
                          status, error_json, created_at, updated_at
                   FROM topic_generation_jobs WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["requested_slot_ids"] = json.loads(result.pop("requested_slot_ids_json"))
        raw_error = result.pop("error_json", None)
        result["error"] = json.loads(raw_error) if raw_error else None
        return result

    def interrupt_running_jobs(self):
        with self.get_connection() as conn:
            conn.execute(
                """UPDATE topic_generation_jobs SET status = 'interrupted', updated_at = ?
                   WHERE status IN ('pending', 'running')""",
                (_now(),),
            )
            conn.commit()

    def save_section_result(self, topic_uid, manifest_hash_value, section, plan=None):
        slot_id = section.get("slot_id")
        content = section.get("content")
        status = section.get("status") or (content or {}).get("status") or "failed"
        evidence_revision = validate_evidence_revision(section.get("evidence_revision"))
        now = _now()
        valid_content = None
        if content is not None:
            valid_content = validate_content(content, slot_id)
            status = valid_content["status"]
        with self.get_connection() as conn:
            cursor = conn.cursor()
            manifest_row = cursor.execute(
                """SELECT manifest_json FROM topic_manifests
                   WHERE topic_uid = ? AND dom_hash = ? LIMIT 1""",
                (topic_uid, manifest_hash_value),
            ).fetchone()
            structure_revision = None
            if manifest_row:
                try:
                    source_manifest = json.loads(manifest_row["manifest_json"])
                    structure_revision = manifest_structure_revisions(source_manifest).get(slot_id)
                except (TypeError, json.JSONDecodeError):
                    pass
            current = cursor.execute(
                """SELECT status FROM topic_content_versions
                   WHERE topic_uid = ? AND slot_id = ? AND is_current = 1
                   ORDER BY id DESC LIMIT 1""",
                (topic_uid, slot_id),
            ).fetchone()
            preserve_ready = bool(
                valid_content is not None
                and valid_content["status"] == "insufficient_evidence"
                and current
                and current["status"] == "ready"
            )
            if valid_content is not None and not preserve_ready:
                cursor.execute(
                    "UPDATE topic_content_versions SET is_current = 0, updated_at = ? WHERE topic_uid = ? AND slot_id = ? AND is_current = 1",
                    (now, topic_uid, slot_id),
                )
            cursor.execute(
                """INSERT INTO topic_content_versions
                   (topic_uid, slot_id, manifest_hash, status, plan_json, content_json, query_id,
                    evidence_digest, evidence_revision, structure_revision, model_json, error_json,
                    is_current, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    topic_uid, slot_id, manifest_hash_value, status,
                    json.dumps(plan, ensure_ascii=False) if plan else None,
                    json.dumps(valid_content, ensure_ascii=False) if valid_content else None,
                    section.get("query_id"), section.get("evidence_digest"),
                    evidence_revision, structure_revision,
                    json.dumps((valid_content or {}).get("model", {}), ensure_ascii=False),
                    json.dumps(section.get("error"), ensure_ascii=False) if section.get("error") else None,
                    1 if valid_content is not None and not preserve_ready else 0, now, now,
                ),
            )
            conn.commit()
        return valid_content

    def get_current_content(self, topic_uid, slot_id):
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT id, status, content_json, evidence_revision, structure_revision, workflow_status,
                          reviewed_by, reviewed_at, published_at, updated_at FROM topic_content_versions
                   WHERE topic_uid = ? AND slot_id = ? AND is_current = 1 ORDER BY id DESC LIMIT 1""",
                (topic_uid, slot_id),
            ).fetchone()
        if not row:
            return None
        return {
            "version_id": row["id"],
            "status": row["status"],
            "content": json.loads(row["content_json"]),
            "evidence_revision": row["evidence_revision"],
            "structure_revision": row["structure_revision"],
            "workflow_status": row["workflow_status"],
            "reviewed_by": row["reviewed_by"],
            "reviewed_at": row["reviewed_at"],
            "published_at": row["published_at"],
            "updated_at": row["updated_at"],
        }

    def list_review_queue(self, topic_uid, workflow_statuses=None):
        """Return bounded current-ready metadata for the medical-review work queue."""
        selected = set(WORKFLOW_STATUSES if workflow_statuses is None else workflow_statuses)
        invalid = selected.difference(WORKFLOW_STATUSES)
        if invalid:
            raise ValueError(f"Unknown workflow status: {sorted(invalid)}")
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        with self.get_connection() as conn:
            rows = conn.execute(
                f"""SELECT id, slot_id, status, workflow_status, content_json,
                            evidence_revision, structure_revision, reviewed_by, reviewed_at,
                            published_at, created_at, updated_at
                     FROM topic_content_versions
                     WHERE topic_uid=? AND is_current=1 AND status='ready'
                       AND workflow_status IN ({placeholders})
                     ORDER BY CASE workflow_status
                         WHEN 'review_pending' THEN 0 WHEN 'approved' THEN 1
                         WHEN 'rejected' THEN 2 WHEN 'generated' THEN 3
                         WHEN 'published' THEN 4 ELSE 5 END,
                         updated_at DESC, id DESC
                     LIMIT 250""",
                (topic_uid, *sorted(selected)),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["version_id"] = item.pop("id")
            content = json.loads(item.pop("content_json")) if item.get("content_json") else None
            item["paper_ids"] = content_paper_ids(content) if content else []
            result.append(item)
        return result

    @staticmethod
    def _assert_current_revisions(row, evidence_revision, structure_revision):
        if row["evidence_revision"] != validate_evidence_revision(evidence_revision):
            raise ValueError("Content evidence revision is stale.")
        if not structure_revision or row["structure_revision"] != structure_revision:
            raise ValueError("Content structure revision is stale.")

    @staticmethod
    def _record_review(conn, row, action, actor, comment, after_status, source_gate=None):
        conn.execute(
            """INSERT INTO topic_content_reviews
               (topic_uid, slot_id, content_version_id, action, actor, comment,
                before_status, after_status, source_gate_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["topic_uid"], row["slot_id"], row["id"], action, actor, comment,
                row["workflow_status"], after_status,
                json.dumps(source_gate, ensure_ascii=False) if source_gate is not None else None,
                _now(),
            ),
        )

    def submit_for_review(self, topic_uid, slot_id, actor, evidence_revision, structure_revision, comment=""):
        actor = _workflow_text(actor, "actor", 120)
        comment = str(comment or "").strip()[:2000]
        now = _now()
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM topic_content_versions
                   WHERE topic_uid=? AND slot_id=? AND is_current=1 ORDER BY id DESC LIMIT 1""",
                (topic_uid, slot_id),
            ).fetchone()
            if not row or row["status"] != "ready":
                raise ValueError("Only current ready content can enter medical review.")
            if row["workflow_status"] not in {"generated", "rejected"}:
                raise ValueError("Content is not eligible for review submission.")
            self._assert_current_revisions(row, evidence_revision, structure_revision)
            self._record_review(conn, row, "submit", actor, comment, "review_pending")
            conn.execute(
                "UPDATE topic_content_versions SET workflow_status='review_pending', updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            conn.commit()
        return self.get_workflow_version(row["id"])

    def decide_review(self, topic_uid, slot_id, actor, decision, comment=""):
        actor = _workflow_text(actor, "actor", 120)
        decision = str(decision or "").strip().lower()
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject.")
        comment = str(comment or "").strip()[:2000]
        after_status = "approved" if decision == "approve" else "rejected"
        now = _now()
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM topic_content_versions
                   WHERE topic_uid=? AND slot_id=? AND is_current=1 ORDER BY id DESC LIMIT 1""",
                (topic_uid, slot_id),
            ).fetchone()
            if not row or row["workflow_status"] != "review_pending":
                raise ValueError("Only review_pending content can be approved or rejected.")
            self._record_review(conn, row, decision, actor, comment, after_status)
            conn.execute(
                """UPDATE topic_content_versions
                   SET workflow_status=?, reviewed_by=?, reviewed_at=?, updated_at=? WHERE id=?""",
                (after_status, actor, now, now, row["id"]),
            )
            conn.commit()
        return self.get_workflow_version(row["id"])

    def publish_current(self, topic_uid, slot_id, actor, evidence_revision, structure_revision, source_gate, comment=""):
        actor = _workflow_text(actor, "actor", 120)
        comment = str(comment or "").strip()[:2000]
        if not isinstance(source_gate, dict) or source_gate.get("allowed") is not True:
            raise ValueError("Commercial source-use gate did not pass.")
        if source_gate.get("required_use") != "commercial_publication":
            raise ValueError("Commercial publication requires the commercial source-use gate.")
        now = _now()
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM topic_content_versions
                   WHERE topic_uid=? AND slot_id=? AND is_current=1 ORDER BY id DESC LIMIT 1""",
                (topic_uid, slot_id),
            ).fetchone()
            if not row or row["workflow_status"] != "approved":
                raise ValueError("Only approved current content can be published.")
            self._assert_current_revisions(row, evidence_revision, structure_revision)
            conn.execute(
                """UPDATE topic_content_versions SET workflow_status='retired', updated_at=?
                   WHERE topic_uid=? AND slot_id=? AND workflow_status='published' AND id<>?""",
                (now, topic_uid, slot_id, row["id"]),
            )
            self._record_review(conn, row, "publish", actor, comment, "published", source_gate)
            conn.execute(
                """UPDATE topic_content_versions
                   SET workflow_status='published', published_at=?, updated_at=? WHERE id=?""",
                (now, now, row["id"]),
            )
            conn.commit()
        return self.get_workflow_version(row["id"])

    def rollback_publication(self, topic_uid, slot_id, version_id, actor, evidence_revision,
                             structure_revision, source_gate, comment=""):
        actor = _workflow_text(actor, "actor", 120)
        comment = str(comment or "").strip()[:2000]
        if not isinstance(source_gate, dict) or source_gate.get("allowed") is not True:
            raise ValueError("Commercial source-use gate did not pass.")
        if source_gate.get("required_use") != "commercial_publication":
            raise ValueError("Commercial publication requires the commercial source-use gate.")
        now = _now()
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM topic_content_versions
                   WHERE id=? AND topic_uid=? AND slot_id=?""",
                (int(version_id), topic_uid, slot_id),
            ).fetchone()
            if not row or row["status"] != "ready" or row["workflow_status"] not in {"retired", "published"}:
                raise ValueError("Rollback target must be a prior publishable version.")
            self._assert_current_revisions(row, evidence_revision, structure_revision)
            conn.execute(
                """UPDATE topic_content_versions SET workflow_status='retired', updated_at=?
                   WHERE topic_uid=? AND slot_id=? AND workflow_status='published' AND id<>?""",
                (now, topic_uid, slot_id, row["id"]),
            )
            self._record_review(conn, row, "rollback", actor, comment, "published", source_gate)
            conn.execute(
                """UPDATE topic_content_versions
                   SET workflow_status='published', published_at=?, updated_at=? WHERE id=?""",
                (now, now, row["id"]),
            )
            conn.commit()
        return self.get_workflow_version(row["id"])

    def get_workflow_version(self, version_id):
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT id, topic_uid, slot_id, status, workflow_status, content_json,
                          evidence_revision, structure_revision, reviewed_by, reviewed_at,
                          published_at, created_at, updated_at
                   FROM topic_content_versions WHERE id=?""",
                (int(version_id),),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["content"] = json.loads(result.pop("content_json")) if result.get("content_json") else None
        result["paper_ids"] = content_paper_ids(result["content"]) if result["content"] else []
        return result

    def get_published_content(self, topic_uid, slot_id):
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT id FROM topic_content_versions
                   WHERE topic_uid=? AND slot_id=? AND workflow_status='published'
                   ORDER BY published_at DESC, id DESC LIMIT 1""",
                (topic_uid, slot_id),
            ).fetchone()
        return self.get_workflow_version(row["id"]) if row else None

    def get_review_history(self, topic_uid, slot_id):
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT id, content_version_id, action, actor, comment, before_status,
                          after_status, source_gate_json, created_at
                   FROM topic_content_reviews WHERE topic_uid=? AND slot_id=? ORDER BY id""",
                (topic_uid, slot_id),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_gate"] = json.loads(item.pop("source_gate_json")) if item.get("source_gate_json") else None
            result.append(item)
        return result

    def get_user_state(self, topic_uid):
        """Return the local user's durable follow/alert checkpoint for one topic."""
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT followed, last_seen_at, last_seen_status_hash, updated_at
                   FROM topic_user_state WHERE topic_uid = ?""",
                (topic_uid,),
            ).fetchone()
        return {
            "followed": bool(row["followed"]) if row else False,
            "last_seen_at": row["last_seen_at"] if row else None,
            "last_seen_status_hash": row["last_seen_status_hash"] if row else None,
            "updated_at": row["updated_at"] if row else None,
        }

    def set_user_state(self, topic_uid, *, followed=None, mark_seen=False, seen_status_hash=None):
        """Upsert only explicit local preferences; clinical content remains in its version table."""
        if seen_status_hash is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", seen_status_hash):
            raise ValueError("seen_status_hash must be a sha256 digest.")
        if mark_seen and seen_status_hash is None:
            raise ValueError("mark_seen requires seen_status_hash.")
        current = self.get_user_state(topic_uid)
        now = _now()
        next_followed = current["followed"] if followed is None else bool(followed)
        next_seen = now if mark_seen else current["last_seen_at"]
        next_seen_hash = seen_status_hash if mark_seen else current["last_seen_status_hash"]
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO topic_user_state
                   (topic_uid, followed, last_seen_at, last_seen_status_hash, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(topic_uid) DO UPDATE SET
                       followed = excluded.followed,
                       last_seen_at = excluded.last_seen_at,
                       last_seen_status_hash = excluded.last_seen_status_hash,
                       updated_at = excluded.updated_at""",
                (topic_uid, int(next_followed), next_seen, next_seen_hash, now),
            )
            conn.commit()
        return self.get_user_state(topic_uid)

    def status_for_topic(self, topic_uid, manifest=None, evidence_revision=None, evidence_revisions=None):
        evidence_revision = validate_evidence_revision(evidence_revision)
        evidence_revisions = validate_evidence_revisions(evidence_revisions)
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT id AS version_id, slot_id, status, manifest_hash, evidence_revision,
                          structure_revision, workflow_status, reviewed_by, reviewed_at,
                          published_at, updated_at
                   FROM topic_content_versions
                   WHERE topic_uid = ? AND is_current = 1""",
                (topic_uid,),
            ).fetchall()
            job = conn.execute(
                """SELECT job_id, status, error_json, created_at, updated_at FROM topic_generation_jobs
                   WHERE topic_uid = ? ORDER BY created_at DESC LIMIT 1""",
                (topic_uid,),
            ).fetchone()
        current_hash = manifest.get("dom_hash") if manifest else None
        current_structure_revisions = manifest_structure_revisions(manifest)
        current_slot_ids = {
            str(slot.get("slot_id"))
            for slot in (manifest or {}).get("slots", [])
            if isinstance(slot, dict) and slot.get("slot_id")
        }

        def stale_reasons(row):
            structure_stale = False
            if manifest:
                current_structure = current_structure_revisions.get(row["slot_id"])
                if row["slot_id"] not in current_slot_ids:
                    structure_stale = True
                elif current_structure and row["structure_revision"]:
                    structure_stale = row["structure_revision"] != current_structure
                else:
                    # Missing structure metadata is a legacy uncertainty, so retain the old over-invalidation rule.
                    structure_stale = bool(current_hash and row["manifest_hash"] != current_hash)
            target_evidence = (evidence_revisions or {}).get(row["slot_id"], evidence_revision)
            evidence_stale = bool(target_evidence and row["evidence_revision"] != target_evidence)
            return [
                reason for reason, changed in (
                    ("structure", structure_stale),
                    ("evidence", evidence_stale),
                ) if changed
            ]

        statuses = {}
        for row in rows:
            reasons = stale_reasons(row)
            statuses[row["slot_id"]] = {
                "status": "stale" if reasons else row["status"],
                "stale_reasons": reasons,
                "version_id": row["version_id"],
                "workflow_status": row["workflow_status"],
                "reviewed_by": row["reviewed_by"],
                "reviewed_at": row["reviewed_at"],
                "published_at": row["published_at"],
                "updated_at": row["updated_at"],
            }
        if manifest:
            for slot in manifest.get("slots", []):
                statuses.setdefault(slot["slot_id"], {
                    "status": "empty",
                    "stale_reasons": [],
                    "version_id": None,
                    "workflow_status": None,
                    "reviewed_by": None,
                    "reviewed_at": None,
                    "published_at": None,
                    "updated_at": None,
                })
        latest_job = None
        if job:
            latest_job = dict(job)
            error_json = latest_job.pop("error_json", None)
            latest_job["error"] = json.loads(error_json) if error_json else None
        return {
            "slots": statuses,
            "latest_job": latest_job,
            "evidence_revision": evidence_revision,
            "evidence_revisions": evidence_revisions,
        }


topic_content_db = TopicContentDatabase()
