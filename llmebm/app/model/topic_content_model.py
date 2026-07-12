# File path: rootmedicals-a/llmebm/app/model/topic_content_model.py
# Description: Versioned dynamic Topic Page manifests, content, and generation jobs.
# ----------------------------------------------------------------------------------------------------

import hashlib
import base64
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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


def _now():
    return datetime.now(timezone.utc).isoformat()


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


def build_manifest(topic_name, topic_uid, tree, viewport=None, screenshots=None):
    slots = []

    def walk(nodes, parent_path=()):
        for node in nodes:
            heading_path = [*parent_path, node["name"]]
            slots.append({
                "slot_id": node["slot_id"],
                "heading": node["name"],
                "heading_path": heading_path,
                "level": node["layer"],
                "order": len(slots),
                "content_target": bool(node.get("content_target", True)),
                "allowed_blocks": list(ALLOWED_BLOCKS),
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
                    model_json TEXT,
                    error_json TEXT,
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
                CREATE INDEX IF NOT EXISTS idx_manifest_current
                    ON topic_manifests(topic_uid, id DESC);
                CREATE INDEX IF NOT EXISTS idx_content_current
                    ON topic_content_versions(topic_uid, slot_id, is_current);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_current_content
                    ON topic_content_versions(topic_uid, slot_id) WHERE is_current = 1;
                CREATE INDEX IF NOT EXISTS idx_topic_jobs
                    ON topic_generation_jobs(topic_uid, created_at DESC);
            """)
            conn.commit()

    def save_manifest(self, manifest):
        canonical = canonical_manifest_fields(manifest)
        dom_hash = manifest_hash(manifest)
        stored = {**manifest, "dom_hash": dom_hash}
        screenshots = [shot.get("sha256", "") for shot in stored.get("screenshots", [])]
        with self.get_connection() as conn:
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
            conn.commit()
        return stored

    def get_current_manifest(self, topic_name):
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM topic_manifests WHERE topic_name = ? ORDER BY id DESC LIMIT 1",
                (topic_name,),
            ).fetchone()
        return json.loads(row["manifest_json"]) if row else None

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
        now = _now()
        valid_content = None
        if content is not None:
            valid_content = validate_content(content, slot_id)
            status = valid_content["status"]
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
                    evidence_digest, model_json, error_json, is_current, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    topic_uid, slot_id, manifest_hash_value, status,
                    json.dumps(plan, ensure_ascii=False) if plan else None,
                    json.dumps(valid_content, ensure_ascii=False) if valid_content else None,
                    section.get("query_id"), section.get("evidence_digest"),
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
                """SELECT status, content_json, updated_at FROM topic_content_versions
                   WHERE topic_uid = ? AND slot_id = ? AND is_current = 1 ORDER BY id DESC LIMIT 1""",
                (topic_uid, slot_id),
            ).fetchone()
        if not row:
            return None
        return {"status": row["status"], "content": json.loads(row["content_json"]), "updated_at": row["updated_at"]}

    def status_for_topic(self, topic_uid, manifest=None):
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT slot_id, status, manifest_hash, updated_at FROM topic_content_versions
                   WHERE topic_uid = ? AND is_current = 1""",
                (topic_uid,),
            ).fetchall()
            job = conn.execute(
                """SELECT job_id, status, error_json, created_at, updated_at FROM topic_generation_jobs
                   WHERE topic_uid = ? ORDER BY created_at DESC LIMIT 1""",
                (topic_uid,),
            ).fetchone()
        current_hash = manifest.get("dom_hash") if manifest else None
        statuses = {
            row["slot_id"]: {
                "status": "stale" if current_hash and row["manifest_hash"] != current_hash else row["status"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }
        if manifest:
            for slot in manifest.get("slots", []):
                statuses.setdefault(slot["slot_id"], {"status": "empty", "updated_at": None})
        latest_job = None
        if job:
            latest_job = dict(job)
            error_json = latest_job.pop("error_json", None)
            latest_job["error"] = json.loads(error_json) if error_json else None
        return {"slots": statuses, "latest_job": latest_job}


topic_content_db = TopicContentDatabase()
