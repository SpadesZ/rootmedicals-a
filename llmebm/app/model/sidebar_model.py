# 檔案路徑: rootmedicals-a/llmebm/app/model/sidebar_model.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmebm 知識庫資料模型，負責 SQLite 知識/taxonomy/sidebar 資料存取。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/llmebm/app/model/sidebar_model.py
# 版本: v0.3
# 更版時間: 2026-05-09 13:00
# 說明: 
#   1. 專屬 Sidebar 知識庫引擎，與 ebm_model.py 徹底解耦。
#   2. [v1.2 重大擴充] 內建 Universal Template (3 層通用公版)。
#   3. 支援 Specialized Features (自建 Layer 1~3 客製化節點) 的增刪改查。
# ----------------------------------------------------------------------------------------------------

import sqlite3
import os
import hashlib
from contextlib import contextmanager
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_DB_DIR = os.path.join(BASE_DIR, "..", "..", "data", "system")
os.makedirs(SYSTEM_DB_DIR, exist_ok=True)
SIDEBAR_DB_PATH = os.path.join(SYSTEM_DB_DIR, "sidebar_menu.db")

# 定義 3 層通用公版 (Universal Template)
UNIVERSAL_TEMPLATE = [
    {"id": "u1", "name": "Overview and Recommendations", "layer": 1, "children": [
        {"id": "u1-1", "name": "Evaluation", "layer": 2, "children": []},
        {"id": "u1-2", "name": "Management", "layer": 2, "children": []}
    ]},
    {"id": "u2", "name": "Background Information", "layer": 1, "children": [
        {"id": "u2-1", "name": "Description", "layer": 2, "children": []},
        {"id": "u2-2", "name": "Epidemiology", "layer": 2, "children": [
            {"id": "u2-2-1", "name": "Incidence/Prevalence", "layer": 3, "children": []}
        ]}
    ]},
    {"id": "u3", "name": "Diagnosis", "layer": 1, "children": [
        {"id": "u3-1", "name": "Making the Diagnosis", "layer": 2, "children": []},
        {"id": "u3-2", "name": "Testing", "layer": 2, "children": [
            {"id": "u3-2-1", "name": "Laboratory Tests", "layer": 3, "children": []},
            {"id": "u3-2-2", "name": "Imaging", "layer": 3, "children": []}
        ]}
    ]},
    {"id": "u4", "name": "Management", "layer": 1, "children": [
        {"id": "u4-1", "name": "Treatment Overview", "layer": 2, "children": []},
        {"id": "u4-2", "name": "Medications", "layer": 2, "children": [
            {"id": "u4-2-1", "name": "First-line therapies", "layer": 3, "children": []},
            {"id": "u4-2-2", "name": "Alternative therapies", "layer": 3, "children": []}
        ]}
    ]},
    {"id": "u5", "name": "Guidelines and Resources", "layer": 1, "children": [
        {"id": "u5-1", "name": "International Guidelines", "layer": 2, "children": []},
        {"id": "u5-2", "name": "United States Guidelines", "layer": 2, "children": [
            {"id": "u5-2-1", "name": "ADA Guidelines", "layer": 3, "children": []}
        ]}
    ]}
]

class SidebarDatabase:
    def __init__(self, db_path=SIDEBAR_DB_PATH):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row 
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
                    parent_id INTEGER,
                    name TEXT NOT NULL,
                    layer_level INTEGER NOT NULL,
                    FOREIGN KEY (parent_id) REFERENCES sidebar_nodes (id)
                )
            ''')
            conn.commit()

    @staticmethod
    def topic_uid_for(topic_name: str, topic_uid: str = "") -> str:
        """Return a stable non-secret UID when the taxonomy row has no UID yet."""
        if topic_uid:
            return topic_uid
        digest = hashlib.sha256(topic_name.strip().casefold().encode("utf-8")).hexdigest()[:16]
        return f"topic-{digest}"

    @staticmethod
    def _with_slot_metadata(node, topic_uid: str, source: str):
        node_id = node["id"]
        return {
            **node,
            "slot_id": f"{topic_uid}:{source}:{node_id}",
            "content_target": True,
            "children": [
                SidebarDatabase._with_slot_metadata(child, topic_uid, source)
                for child in node.get("children", [])
            ],
        }

    def get_sidebar_tree(self, topic_name: str, topic_uid: str = ""):
        """回傳包含 Universal Template 與 Custom Nodes (Specialized Features) 的複合字典"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, parent_id, name, layer_level FROM sidebar_nodes WHERE topic_name = ? ORDER BY id ASC", (topic_name,))
            all_custom_nodes = [dict(row) for row in cursor.fetchall()]
            
        custom_tree = []
        if all_custom_nodes:
            children_map = defaultdict(list)
            root_nodes = []
            for node in all_custom_nodes:
                if node['parent_id'] is None:
                    root_nodes.append(node)
                else:
                    children_map[node['parent_id']].append(node)
                    
            def build_custom_tree(node_id):
                children = children_map.get(node_id, [])
                node_list = []
                for child in children:
                    node_list.append({
                        "id": child['id'],
                        "name": child['name'],
                        "layer": child['layer_level'],
                        "children": build_custom_tree(child['id'])
                    })
                return node_list

            for root in root_nodes:
                custom_tree.append({
                    "id": root['id'],
                    "name": root['name'],
                    "layer": root['layer_level'],
                    "children": build_custom_tree(root['id'])
                })
                
        stable_topic_uid = self.topic_uid_for(topic_name, topic_uid)
        return {
            "topic_uid": stable_topic_uid,
            "universal": [
                self._with_slot_metadata(node, stable_topic_uid, "universal")
                for node in UNIVERSAL_TEMPLATE
            ],
            "custom": [
                self._with_slot_metadata(node, stable_topic_uid, "custom")
                for node in custom_tree
            ],
        }

    def get_custom_nodes_flat(self, topic_name: str):
        """取得供下拉選單使用的平坦化客製節點清單 (Layer 1 & 2)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, layer_level FROM sidebar_nodes WHERE topic_name = ? AND layer_level < 3 ORDER BY layer_level ASC, name ASC", (topic_name,))
            return [dict(row) for row in cursor.fetchall()]

    def add_custom_node(self, topic_name: str, parent_id: int, name: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            layer_level = 1
            if parent_id is not None:
                cursor.execute("SELECT layer_level FROM sidebar_nodes WHERE id = ?", (parent_id,))
                row = cursor.fetchone()
                if not row: raise ValueError("Parent ID not found.")
                layer_level = row['layer_level'] + 1
                if layer_level > 3: raise ValueError("Sidebar only supports up to 3 layers.")
                
            cursor.execute("INSERT INTO sidebar_nodes (topic_name, parent_id, name, layer_level) VALUES (?, ?, ?, ?)", (topic_name, parent_id, name.strip(), layer_level))
            conn.commit()
            return cursor.lastrowid

    def delete_custom_node(self, node_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT topic_name FROM sidebar_nodes WHERE id = ?", (node_id,))
            node = cursor.fetchone()
            if not node:
                raise ValueError("Sidebar node not found.")
            cursor.execute("SELECT COUNT(*) FROM sidebar_nodes WHERE parent_id = ?", (node_id,))
            if cursor.fetchone()[0] > 0:
                raise ValueError("Cannot delete node: It contains active sub-categories.")
            cursor.execute("DELETE FROM sidebar_nodes WHERE id = ?", (node_id,))
            conn.commit()
            return node["topic_name"]

sidebar_db = SidebarDatabase()
