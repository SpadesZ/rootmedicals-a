# 模組定位: llmebm Topic Page 的 sidebar taxonomy 與穩定 slot identity 持久層。
# 主要責任: 提供通用模板、自建節點 CRUD、穩定 topic/slot UID、heading migration 與參考能力匯入稽核。
# 呼叫來源: main_ebm.py 的 Topic Page/sidebar 路由與 topic manifest 建立流程。
# 輸入契約: topic 名稱、taxonomy UID、合法 parent/heading，或已核准且不含外部內容的能力 plan hashes。
# 輸出契約: universal/custom tree 與不改 hierarchy 的 reference import audit receipt。
# 安全邊界: SQL 全部參數化；canonicalization 不執行路徑或 HTML 解譯。
# 維護提醒: topic 名稱 aliases 必須先經 canonical_topic_slug，避免重複 manifest 與分裂內容版本。
# ----------------------------------------------------------------------------------------------------

import sqlite3
import os
import hashlib
import json
import re
import unicodedata
from contextlib import contextmanager
from collections import defaultdict
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_DB_DIR = os.path.join(BASE_DIR, "..", "..", "data", "system")
os.makedirs(SYSTEM_DB_DIR, exist_ok=True)
SIDEBAR_DB_PATH = os.getenv("LLMEBM_SIDEBAR_DB_PATH", os.path.join(SYSTEM_DB_DIR, "sidebar_menu.db"))
REFERENCE_IMPORT_CAPABILITIES = ("interactive_topic_tabs", "back_to_top")

MIGRATIONS_DIR = Path(BASE_DIR) / "migrations"
DEFAULT_ALLOWED_BLOCKS_JSON = json.dumps(
    ["summary", "recommendations", "bullets", "evidence_note", "table", "warning"],
    ensure_ascii=False,
    separators=(",", ":"),
)
SIDEBAR_SCHEMA_VERSION = "sidebar.v2"

def canonical_topic_slug(topic_name: str) -> str:
    """Map display-name and URL variants to one stable Unicode-safe topic key."""
    normalized = unicodedata.normalize("NFKC", str(topic_name or "")).strip().casefold()
    slug = re.sub(r"[\W_]+", "-", normalized, flags=re.UNICODE).strip("-")
    if not slug:
        raise ValueError("Topic name must contain at least one letter or number.")
    return slug

class SidebarDatabase:
    def __init__(self, db_path=SIDEBAR_DB_PATH):
        self.db_path = db_path
        self._init_db()

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
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sidebar_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_name TEXT NOT NULL,
                    topic_uid TEXT,
                    parent_id INTEGER,
                    node_key TEXT,
                    source TEXT NOT NULL DEFAULT 'custom',
                    name TEXT NOT NULL,
                    layer_level INTEGER NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    content_target INTEGER NOT NULL DEFAULT 1,
                    allowed_blocks_json TEXT NOT NULL DEFAULT '["summary","recommendations","bullets","evidence_note","table","warning"]',
                    status TEXT NOT NULL DEFAULT 'published',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (parent_id) REFERENCES sidebar_nodes (id)
                )
            ''')
            existing_columns = {
                row["name"] for row in cursor.execute("PRAGMA table_info(sidebar_nodes)").fetchall()
            }
            legacy_schema = "node_key" not in existing_columns
            for column_name, definition in (
                ("topic_uid", "TEXT"),
                ("node_key", "TEXT"),
                ("source", "TEXT NOT NULL DEFAULT 'custom'"),
                ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
                ("content_target", "INTEGER NOT NULL DEFAULT 1"),
                ("allowed_blocks_json", f"TEXT NOT NULL DEFAULT '{DEFAULT_ALLOWED_BLOCKS_JSON}'"),
                ("status", "TEXT NOT NULL DEFAULT 'published'"),
                ("created_at", "TEXT"),
                ("updated_at", "TEXT"),
            ):
                if column_name not in existing_columns:
                    cursor.execute(f"ALTER TABLE sidebar_nodes ADD COLUMN {column_name} {definition}")

            cursor.execute(
                """UPDATE sidebar_nodes
                   SET allowed_blocks_json = COALESCE(allowed_blocks_json, ?),
                       status = COALESCE(status, 'published'),
                       created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
                       updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)""",
                (DEFAULT_ALLOWED_BLOCKS_JSON,),
            )

            # ponytail: legacy rows remain custom because guessing their origin could change every existing slot_id.
            # If a future import can prove provenance, promote rows only through an explicit identity-aware migration.
            rows = cursor.execute(
                """SELECT id, topic_name, topic_uid, node_key, source, sort_order
                   FROM sidebar_nodes ORDER BY id"""
            ).fetchall()
            for row in rows:
                canonical = canonical_topic_slug(row["topic_name"])
                source = row["source"] if row["source"] in {"universal", "custom"} else "custom"
                cursor.execute(
                    """UPDATE sidebar_nodes
                       SET topic_name = ?, topic_uid = ?, node_key = ?, source = ?, sort_order = ?
                       WHERE id = ?""",
                    (
                        canonical,
                        row["topic_uid"] or self.topic_uid_for(canonical),
                        row["node_key"] or str(row["id"]),
                        source,
                        row["id"] if legacy_schema else row["sort_order"],
                        row["id"],
                    ),
                )
            cursor.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_sidebar_slot_key
                   ON sidebar_nodes(topic_uid, source, node_key)
                   WHERE topic_uid IS NOT NULL AND node_key IS NOT NULL"""
            )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS idx_sidebar_tree
                   ON sidebar_nodes(topic_name, source, parent_id, sort_order, id)"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS reference_structure_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_uid TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    comparison_hash TEXT NOT NULL,
                    approved_capabilities_json TEXT NOT NULL,
                    slot_ids_sha256 TEXT NOT NULL,
                    slot_count INTEGER NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(topic_uid, plan_hash)
                )"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS hierarchy_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_uid TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    comment TEXT,
                    before_hash TEXT NOT NULL,
                    after_hash TEXT NOT NULL,
                    before_snapshot_json TEXT NOT NULL,
                    after_snapshot_json TEXT NOT NULL,
                    rollback_of INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(rollback_of) REFERENCES hierarchy_audit_log(id)
                )"""
            )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS idx_hierarchy_audit_topic
                   ON hierarchy_audit_log(topic_uid, id DESC)"""
            )
            conn.commit()
            self._apply_migrations(conn)

    @staticmethod
    def _apply_migrations(conn):
        """Apply immutable SQL taxonomy artifacts once and reject edited history."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.commit()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if not re.fullmatch(r"\d{3}_[a-z0-9_]+", version):
                raise RuntimeError(f"Invalid sidebar migration filename: {path.name}")
            sql = path.read_text(encoding="utf-8")
            checksum = "sha256:" + hashlib.sha256(sql.encode("utf-8")).hexdigest()
            stored = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if stored:
                if stored["checksum"] != checksum:
                    raise RuntimeError(f"Applied sidebar migration checksum changed: {version}")
                continue
            # ponytail: migrations are tiny local SQL files; one transaction keeps taxonomy and receipt atomic.
            receipt = (
                "INSERT INTO schema_migrations(version, schema_version, checksum) VALUES ("
                f"'{version}', '{SIDEBAR_SCHEMA_VERSION}', '{checksum}');"
            )
            try:
                conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\n{receipt}\nCOMMIT;")
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def topic_uid_for(topic_name: str, topic_uid: str = "") -> str:
        """Return a stable non-secret UID when the taxonomy row has no UID yet."""
        if topic_uid:
            return topic_uid
        digest = hashlib.sha256(canonical_topic_slug(topic_name).encode("utf-8")).hexdigest()[:16]
        return f"topic-{digest}"

    @staticmethod
    def _with_slot_metadata(node, topic_uid: str, source: str):
        node_id = node["id"]
        return {
            **node,
            "slot_id": f"{topic_uid}:{source}:{node_id}",
            "content_target": bool(node.get("content_target", True)),
            "children": [
                SidebarDatabase._with_slot_metadata(child, topic_uid, source)
                for child in node.get("children", [])
            ],
        }

    @staticmethod
    def _tree_from_rows(rows, source: str):
        """Rebuild the three-level response tree while retaining DB row IDs for custom CRUD."""
        children_map = defaultdict(list)
        root_nodes = []
        for row in rows:
            node = dict(row)
            if node["parent_id"] is None:
                root_nodes.append(node)
            else:
                children_map[node["parent_id"]].append(node)

        def build(node):
            return {
                "id": node["node_key"] if source == "universal" else node["id"],
                "name": node["name"],
                "layer": node["layer_level"],
                "content_target": bool(node["content_target"]),
                "allowed_blocks": SidebarDatabase._decode_allowed_blocks(node["allowed_blocks_json"]),
                "children": [build(child) for child in children_map.get(node["id"], [])],
            }

        return [build(node) for node in root_nodes]

    @staticmethod
    def _decode_allowed_blocks(value):
        try:
            blocks = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("sidebar_nodes.allowed_blocks_json must be valid JSON.") from exc
        if not isinstance(blocks, list) or not blocks or any(
            not isinstance(block, str) or not block for block in blocks
        ):
            raise RuntimeError("sidebar_nodes.allowed_blocks_json must be a non-empty string list.")
        return blocks

    def _materialize_template_if_missing(self, conn, topic_name: str, topic_uid: str):
        """Create a new topic from the SQL-owned condition template without correcting existing rows."""
        cursor = conn.cursor()
        exists = cursor.execute(
            "SELECT 1 FROM sidebar_nodes WHERE topic_uid = ? AND source = 'universal' LIMIT 1",
            (topic_uid,),
        ).fetchone()
        if exists:
            return
        rows = cursor.execute(
            """SELECT node_key, parent_node_key, name, layer_level, sort_order,
                      content_target, allowed_blocks_json, status
               FROM sidebar_topic_seed_nodes
               WHERE topic_uid = ? AND status = 'published'
               ORDER BY layer_level, sort_order, node_key""",
            (topic_uid,),
        ).fetchall()
        if not rows:
            rows = cursor.execute(
                """SELECT node_key, parent_node_key, name, layer_level, sort_order,
                          content_target, allowed_blocks_json, status
                   FROM sidebar_template_nodes
                   WHERE template_key = 'condition.v1' AND status = 'published'
                   ORDER BY layer_level, sort_order, node_key"""
            ).fetchall()
        if not rows:
            raise RuntimeError("condition.v1 hierarchy template is missing.")
        inserted = {}
        for row in rows:
            parent_key = row["parent_node_key"]
            if parent_key and parent_key not in inserted:
                raise RuntimeError(f"Hierarchy template parent missing before child: {parent_key}")
            cursor.execute(
                """INSERT INTO sidebar_nodes
                   (topic_name, topic_uid, parent_id, node_key, source, name, layer_level,
                    sort_order, content_target, allowed_blocks_json, status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'universal', ?, ?, ?, ?, ?, ?,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (
                    topic_name, topic_uid, inserted.get(parent_key), row["node_key"], row["name"],
                    row["layer_level"], row["sort_order"], row["content_target"],
                    row["allowed_blocks_json"], row["status"],
                ),
            )
            inserted[row["node_key"]] = cursor.lastrowid

    def get_sidebar_tree(self, topic_name: str, topic_uid: str = ""):
        """回傳包含 Universal Template 與 Custom Nodes (Specialized Features) 的複合字典"""
        topic_name = canonical_topic_slug(topic_name)
        stable_topic_uid = self.topic_uid_for(topic_name, topic_uid)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._materialize_template_if_missing(conn, topic_name, stable_topic_uid)
            rows = cursor.execute(
                """SELECT id, parent_id, node_key, source, name, layer_level, content_target,
                          allowed_blocks_json
                   FROM sidebar_nodes WHERE topic_name = ? AND status = 'published'
                   ORDER BY source, sort_order, id""",
                (topic_name,),
            ).fetchall()
            conn.commit()
        universal_tree = self._tree_from_rows(
            (row for row in rows if row["source"] == "universal"), "universal"
        )
        custom_tree = self._tree_from_rows(
            (row for row in rows if row["source"] == "custom"), "custom"
        )
        return {
            "topic_uid": stable_topic_uid,
            "universal": [
                self._with_slot_metadata(node, stable_topic_uid, "universal")
                for node in universal_tree
            ],
            "custom": [
                self._with_slot_metadata(node, stable_topic_uid, "custom")
                for node in custom_tree
            ],
        }

    def search_topics_and_sections(self, query: str, limit: int = 20):
        """Search only llmebm-owned topic/heading rows and return stable deep-link targets."""
        normalized = str(query or "").strip()
        if len(normalized) < 2:
            return []
        limit = max(1, min(int(limit), 50))
        escaped = normalized.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT topic_name, topic_uid, node_key, source, name, layer_level
                   FROM sidebar_nodes
                   WHERE status = 'published'
                     AND (lower(topic_name) LIKE ? ESCAPE '\\' OR lower(name) LIKE ? ESCAPE '\\')
                   ORDER BY CASE WHEN lower(name) = ? THEN 0 ELSE 1 END,
                            topic_name, layer_level, sort_order, id
                   LIMIT ?""",
                (pattern, pattern, normalized.casefold(), limit),
            ).fetchall()
        return [{
            "topic_name": row["topic_name"],
            "topic_uid": row["topic_uid"],
            "heading": row["name"],
            "level": row["layer_level"],
            "slot_id": (
                f"{row['topic_uid']}:{row['source']}:"
                f"{row['node_key'] if row['source'] == 'universal' else row['node_key']}"
            ),
        } for row in rows]

    @staticmethod
    def _require_sha256(value: str, field_name: str):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or "")):
            raise ValueError(f"{field_name} must be a sha256 digest.")

    @staticmethod
    def _slot_identity_snapshot(conn, topic_uid: str):
        rows = conn.execute(
            """SELECT id, node_key, source FROM sidebar_nodes
               WHERE topic_uid = ? ORDER BY source, id""",
            (topic_uid,),
        ).fetchall()
        slot_ids = sorted(
            f"{topic_uid}:{row['source']}:"
            f"{row['node_key'] if row['source'] == 'universal' else row['id']}"
            for row in rows
        )
        canonical = json.dumps(slot_ids, ensure_ascii=False, separators=(",", ":"))
        return {
            "topic_uid": topic_uid,
            "slot_ids": slot_ids,
            "slot_count": len(slot_ids),
            "slot_ids_sha256": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }

    def reference_import_snapshot(self, topic_name: str, topic_uid: str = ""):
        """Return the exact current slot set used as the before/after import gate."""
        canonical = canonical_topic_slug(topic_name)
        stable_topic_uid = self.topic_uid_for(canonical, topic_uid)
        with self.get_connection() as conn:
            self._materialize_template_if_missing(conn, canonical, stable_topic_uid)
            snapshot = self._slot_identity_snapshot(conn, stable_topic_uid)
            conn.commit()
        return snapshot

    def record_reference_import(
        self,
        *,
        topic_name: str,
        comparison_hash: str,
        plan_hash: str,
        approved_capabilities,
        expected_slot_ids_sha256: str,
        manifest_hash: str,
        topic_uid: str = "",
    ):
        """Atomically record an approved capability-only import without changing the hierarchy."""
        for field_name, value in (
            ("comparison_hash", comparison_hash),
            ("plan_hash", plan_hash),
            ("expected_slot_ids_sha256", expected_slot_ids_sha256),
            ("manifest_hash", manifest_hash),
        ):
            self._require_sha256(value, field_name)
        if not isinstance(approved_capabilities, (list, tuple)):
            raise ValueError("approved_capabilities must be a list or tuple.")
        if len(approved_capabilities) > len(REFERENCE_IMPORT_CAPABILITIES) or any(
            not isinstance(item, str) for item in approved_capabilities
        ):
            raise ValueError("approved_capabilities contains invalid values.")
        approved_set = set(approved_capabilities)
        if not approved_set or len(approved_set) != len(approved_capabilities):
            raise ValueError("approved_capabilities must contain unique reviewed capabilities.")
        unsupported = approved_set - set(REFERENCE_IMPORT_CAPABILITIES)
        if unsupported:
            raise ValueError("approved_capabilities contains an unapproved capability.")
        approved = [item for item in REFERENCE_IMPORT_CAPABILITIES if item in approved_set]
        approved_json = json.dumps(approved, ensure_ascii=False, separators=(",", ":"))
        canonical = canonical_topic_slug(topic_name)
        stable_topic_uid = self.topic_uid_for(canonical, topic_uid)

        # ponytail: this table is audit-only; if neutral hierarchy nodes are later approved,
        # add a separately reviewed identity migration instead of teaching this path to mutate slots.
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._materialize_template_if_missing(conn, canonical, stable_topic_uid)
                snapshot = self._slot_identity_snapshot(conn, stable_topic_uid)
                if snapshot["slot_ids_sha256"] != expected_slot_ids_sha256:
                    raise ValueError("slot identity changed before reference import audit.")
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO reference_structure_imports
                       (topic_uid, plan_hash, comparison_hash, approved_capabilities_json,
                        slot_ids_sha256, slot_count, manifest_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        stable_topic_uid, plan_hash, comparison_hash, approved_json,
                        snapshot["slot_ids_sha256"], snapshot["slot_count"], manifest_hash,
                    ),
                )
                stored = conn.execute(
                    """SELECT comparison_hash, approved_capabilities_json, slot_ids_sha256,
                              slot_count, manifest_hash
                       FROM reference_structure_imports
                       WHERE topic_uid = ? AND plan_hash = ?""",
                    (stable_topic_uid, plan_hash),
                ).fetchone()
                expected = (
                    comparison_hash, approved_json, snapshot["slot_ids_sha256"],
                    snapshot["slot_count"], manifest_hash,
                )
                if stored is None or tuple(stored) != expected:
                    raise ValueError("existing reference import audit does not match this plan.")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "status": "applied" if cursor.rowcount == 1 else "unchanged",
            "topic_uid": stable_topic_uid,
            "plan_hash": plan_hash,
            "slot_count": snapshot["slot_count"],
            "slot_ids_sha256": snapshot["slot_ids_sha256"],
        }

    def get_custom_nodes_flat(self, topic_name: str):
        """取得供下拉選單使用的平坦化客製節點清單 (Layer 1 & 2)"""
        topic_name = canonical_topic_slug(topic_name)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, name, layer_level FROM sidebar_nodes
                   WHERE topic_name = ? AND source = 'custom' AND layer_level < 3
                   ORDER BY layer_level ASC, name ASC""",
                (topic_name,),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _hierarchy_hash(rows):
        encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _hierarchy_rows(conn, topic_uid):
        return [dict(row) for row in conn.execute(
            """SELECT id, topic_name, topic_uid, parent_id, node_key, source, name,
                      layer_level, sort_order, content_target, allowed_blocks_json, status
               FROM sidebar_nodes WHERE topic_uid=? ORDER BY id""",
            (topic_uid,),
        ).fetchall()]

    @staticmethod
    def _validate_hierarchy_rows(rows):
        by_id = {row["id"]: row for row in rows}
        if len(by_id) != len(rows):
            raise ValueError("Hierarchy contains duplicate node IDs.")
        for row in rows:
            if row["source"] not in {"universal", "custom"}:
                raise ValueError("Hierarchy contains an invalid source.")
            if row["status"] not in {"draft", "published", "retired"}:
                raise ValueError("Hierarchy contains an invalid lifecycle status.")
            if not str(row["name"] or "").strip() or not str(row["node_key"] or "").strip():
                raise ValueError("Hierarchy node name/node_key is required.")
            if not isinstance(row["sort_order"], int) or row["sort_order"] < 0:
                raise ValueError("Hierarchy sort_order must be non-negative.")
            SidebarDatabase._decode_allowed_blocks(row["allowed_blocks_json"])
            parent = by_id.get(row["parent_id"]) if row["parent_id"] is not None else None
            expected_level = 1 if parent is None else parent["layer_level"] + 1
            if row["parent_id"] is not None and parent is None:
                raise ValueError("Hierarchy contains an orphan node.")
            if parent and (
                parent["topic_uid"] != row["topic_uid"]
                or parent["topic_name"] != row["topic_name"]
                or parent["source"] != row["source"]
            ):
                raise ValueError("Hierarchy parent crosses topic or source.")
            seen = {row["id"]}
            cursor = parent
            while cursor is not None:
                if cursor["id"] in seen:
                    raise ValueError("Hierarchy contains a cycle.")
                seen.add(cursor["id"])
                cursor = by_id.get(cursor["parent_id"]) if cursor["parent_id"] is not None else None
            if row["layer_level"] != expected_level:
                raise ValueError("Hierarchy layer_level does not match its parent.")
            if row["source"] == "custom" and row["layer_level"] > 3:
                raise ValueError("Custom hierarchy only supports three layers.")
        return rows

    @classmethod
    def _simulate_hierarchy_change(cls, rows, source, node_key, change):
        if not isinstance(change, dict) or change.get("type") not in {"rename", "reorder", "move", "retire", "restore"}:
            raise ValueError("Hierarchy change type is invalid.")
        next_rows = [dict(row) for row in rows]
        by_id = {row["id"]: row for row in next_rows}
        targets = [row for row in next_rows if row["source"] == source and row["node_key"] == node_key]
        if len(targets) != 1:
            raise ValueError("Hierarchy target was not found uniquely.")
        target = targets[0]
        change_type = change["type"]
        if change_type == "rename":
            name = " ".join(str(change.get("name") or "").split()).strip()
            if not name or len(name) > 240:
                raise ValueError("Hierarchy name must be 1..240 characters.")
            target["name"] = name
        elif change_type == "reorder":
            order = change.get("sort_order")
            if not isinstance(order, int) or isinstance(order, bool) or order < 0:
                raise ValueError("Hierarchy sort_order must be a non-negative integer.")
            target["sort_order"] = order
        elif change_type == "move":
            parent_key = change.get("parent_node_key")
            parent = None
            if parent_key is not None:
                candidates = [
                    row for row in next_rows
                    if row["source"] == source and row["node_key"] == str(parent_key)
                ]
                if len(candidates) != 1:
                    raise ValueError("Hierarchy move parent was not found uniquely.")
                parent = candidates[0]
            old_level = target["layer_level"]
            target["parent_id"] = parent["id"] if parent else None
            target["layer_level"] = 1 if parent is None else parent["layer_level"] + 1
            delta = target["layer_level"] - old_level
            descendants = {target["id"]}
            changed = True
            while changed:
                changed = False
                for row in next_rows:
                    if row["parent_id"] in descendants and row["id"] not in descendants:
                        descendants.add(row["id"])
                        changed = True
            for row in next_rows:
                if row["id"] in descendants and row["id"] != target["id"]:
                    row["layer_level"] += delta
        elif change_type == "retire":
            if any(row["parent_id"] == target["id"] and row["status"] == "published" for row in next_rows):
                raise ValueError("Retire published children before retiring their parent.")
            target["status"] = "retired"
        else:
            parent = by_id.get(target["parent_id"]) if target["parent_id"] is not None else None
            if parent and parent["status"] != "published":
                raise ValueError("Restore the parent before restoring this node.")
            target["status"] = "published"
        return cls._validate_hierarchy_rows(next_rows)

    def preview_hierarchy_change(self, topic_name, source, node_key, change, topic_uid=""):
        canonical = canonical_topic_slug(topic_name)
        stable_uid = self.topic_uid_for(canonical, topic_uid)
        with self.get_connection() as conn:
            self._materialize_template_if_missing(conn, canonical, stable_uid)
            before = self._validate_hierarchy_rows(self._hierarchy_rows(conn, stable_uid))
            conn.commit()
        after = self._simulate_hierarchy_change(before, source, str(node_key), change)
        return {
            "topic_uid": stable_uid,
            "action": change["type"],
            "before_hash": self._hierarchy_hash(before),
            "after_hash": self._hierarchy_hash(after),
            "changed_node_keys": [
                row["node_key"] for row, prior in zip(after, before) if row != prior
            ],
            "before": before,
            "after": after,
        }

    def apply_hierarchy_change(self, topic_name, source, node_key, change, actor,
                               expected_before_hash, comment="", topic_uid=""):
        actor = " ".join(str(actor or "").split()).strip()
        if not actor or len(actor) > 120:
            raise ValueError("actor must be 1..120 characters.")
        self._require_sha256(expected_before_hash, "expected_before_hash")
        preview = self.preview_hierarchy_change(topic_name, source, node_key, change, topic_uid)
        if preview["before_hash"] != expected_before_hash:
            raise ValueError("Hierarchy changed after preview; refresh before applying.")
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                current = self._validate_hierarchy_rows(self._hierarchy_rows(conn, preview["topic_uid"]))
                if self._hierarchy_hash(current) != expected_before_hash:
                    raise ValueError("Hierarchy changed after preview; refresh before applying.")
                for row in preview["after"]:
                    conn.execute(
                        """UPDATE sidebar_nodes SET parent_id=?, name=?, layer_level=?, sort_order=?,
                                  status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (row["parent_id"], row["name"], row["layer_level"], row["sort_order"], row["status"], row["id"]),
                    )
                cursor = conn.execute(
                    """INSERT INTO hierarchy_audit_log
                       (topic_uid, action, actor, comment, before_hash, after_hash,
                        before_snapshot_json, after_snapshot_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        preview["topic_uid"], preview["action"], actor, str(comment or "")[:2000],
                        preview["before_hash"], preview["after_hash"],
                        json.dumps(preview["before"], ensure_ascii=False),
                        json.dumps(preview["after"], ensure_ascii=False),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {**preview, "audit_id": cursor.lastrowid, "before": None, "after": None}

    def rollback_hierarchy_change(self, audit_id, actor, comment="", expected_topic_uid=""):
        actor = " ".join(str(actor or "").split()).strip()
        if not actor or len(actor) > 120:
            raise ValueError("actor must be 1..120 characters.")
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                audit = conn.execute("SELECT * FROM hierarchy_audit_log WHERE id=?", (int(audit_id),)).fetchone()
                if not audit:
                    raise ValueError("Hierarchy audit entry was not found.")
                if expected_topic_uid and audit["topic_uid"] != expected_topic_uid:
                    raise ValueError("Hierarchy audit entry belongs to a different topic.")
                current = self._validate_hierarchy_rows(self._hierarchy_rows(conn, audit["topic_uid"]))
                if self._hierarchy_hash(current) != audit["after_hash"]:
                    raise ValueError("Hierarchy changed after this audit; rollback would overwrite newer work.")
                before = self._validate_hierarchy_rows(json.loads(audit["before_snapshot_json"]))
                for row in before:
                    conn.execute(
                        """UPDATE sidebar_nodes SET parent_id=?, name=?, layer_level=?, sort_order=?,
                                  status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (row["parent_id"], row["name"], row["layer_level"], row["sort_order"], row["status"], row["id"]),
                    )
                after_hash = self._hierarchy_hash(before)
                cursor = conn.execute(
                    """INSERT INTO hierarchy_audit_log
                       (topic_uid, action, actor, comment, before_hash, after_hash,
                        before_snapshot_json, after_snapshot_json, rollback_of)
                       VALUES (?, 'rollback', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        audit["topic_uid"], actor, str(comment or "")[:2000], audit["after_hash"], after_hash,
                        audit["after_snapshot_json"], audit["before_snapshot_json"], audit["id"],
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"status": "rolled_back", "audit_id": cursor.lastrowid, "rollback_of": int(audit_id), "after_hash": after_hash}

    def add_custom_node(self, topic_name: str, parent_id: int, name: str):
        topic_name = canonical_topic_slug(topic_name)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            layer_level = 1
            if parent_id is not None:
                cursor.execute(
                    """SELECT layer_level FROM sidebar_nodes
                       WHERE id = ? AND topic_name = ? AND source = 'custom'""",
                    (parent_id, topic_name),
                )
                row = cursor.fetchone()
                if not row: raise ValueError("Parent ID not found.")
                layer_level = row['layer_level'] + 1
                if layer_level > 3: raise ValueError("Sidebar only supports up to 3 layers.")

            cursor.execute(
                """INSERT INTO sidebar_nodes
                   (topic_name, topic_uid, parent_id, node_key, source, name,
                    layer_level, sort_order, content_target)
                   VALUES (?, ?, ?, NULL, 'custom', ?, ?, 0, 1)""",
                (topic_name, self.topic_uid_for(topic_name), parent_id, name.strip(), layer_level),
            )
            node_id = cursor.lastrowid
            cursor.execute(
                "UPDATE sidebar_nodes SET node_key = ?, sort_order = ? WHERE id = ?",
                (str(node_id), node_id, node_id),
            )
            conn.commit()
            return node_id

    def delete_custom_node(self, node_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT topic_name FROM sidebar_nodes WHERE id = ? AND source = 'custom'",
                (node_id,),
            )
            node = cursor.fetchone()
            if not node:
                raise ValueError("Sidebar node not found.")
            cursor.execute(
                "SELECT COUNT(*) FROM sidebar_nodes WHERE parent_id = ? AND source = 'custom'",
                (node_id,),
            )
            if cursor.fetchone()[0] > 0:
                raise ValueError("Cannot delete node: It contains active sub-categories.")
            cursor.execute("DELETE FROM sidebar_nodes WHERE id = ? AND source = 'custom'", (node_id,))
            conn.commit()
            return node["topic_name"]

sidebar_db = SidebarDatabase()
