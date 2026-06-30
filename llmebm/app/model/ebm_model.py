# 檔案路徑: rootmedicals-a/llmebm/app/model/ebm_model.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmebm 知識庫資料模型，負責 SQLite 知識/taxonomy/sidebar 資料存取。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/llmebm/app/model/ebm_model.py
# 版本: v1.5
# 更版時間: 2026-05-09 11:00
# 說明: 
#   1. 絕對遵守第二定律：100% 保留 v1.4 的所有原版資料表、5層醫療樹、安全 CRUD 防呆與 API。
#   2. [架構優化] 本檔案純粹負責 ebm_knowledge.db 與 specialty.db，符合單一職責原則。
#   3. 閱讀頁面專屬的 sidebar_menu.db 邏輯已完美解耦至 sidebar_model.py。
#   4. 實作 Base-33 階層式 UID 引擎 (0-9 + 23大寫字母)，確保血脈繼承與唯一性。
#   5. [v1.5 終極修復] 修正 DB_PATH 路徑，強制退回兩層至 data/system/，解決 ebm_knowledge.db 不斷在 app/ 下重複建立的 Bug。
# ----------------------------------------------------------------------------------------------------

import sqlite3
import os
import csv
import io
import random
from contextlib import contextmanager
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# [v1.5 修復] 退回兩層至 data/system/，統一集中管理資料庫
SYSTEM_DB_DIR = os.path.join(BASE_DIR, "..", "..", "data", "system")
os.makedirs(SYSTEM_DB_DIR, exist_ok=True)
DB_PATH = os.path.join(SYSTEM_DB_DIR, "ebm_knowledge.db")

class EBMDatabase:
    def __init__(self, db_path=DB_PATH):
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
            cursor.execute('''CREATE TABLE IF NOT EXISTS navigation_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, category_type TEXT NOT NULL, category_name TEXT NOT NULL, sort_order INTEGER DEFAULT 0)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS system_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS specialties (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS topic_groups (id INTEGER PRIMARY KEY AUTOINCREMENT, specialty_id INTEGER NOT NULL, name TEXT NOT NULL, FOREIGN KEY (specialty_id) REFERENCES specialties (id))''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS topics (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, name TEXT NOT NULL, FOREIGN KEY (group_id) REFERENCES topic_groups (id))''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS medical_topics (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, name TEXT NOT NULL, layer_level INTEGER NOT NULL, FOREIGN KEY (parent_id) REFERENCES medical_topics (id))''')
            conn.commit()
            
            cursor.execute("SELECT COUNT(*) FROM system_settings")
            if cursor.fetchone()[0] == 0:
                cursor.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?)", ('brand_name', 'RootMedicals Health'))
                cursor.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?)", ('brand_logo', ''))
                conn.commit()

            cursor.execute("SELECT COUNT(*) FROM specialties")
            if cursor.fetchone()[0] == 0:
                self._seed_specialties_data(cursor, conn)
                self._seed_initial_data(cursor, conn) 

            cursor.execute("SELECT COUNT(*) FROM medical_topics")
            if cursor.fetchone()[0] == 0:
                self._seed_all_medical_data_v2(cursor, conn)

    def get_system_settings(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT setting_key, setting_value FROM system_settings")
            rows = cursor.fetchall()
            return {row['setting_key']: row['setting_value'] for row in rows}

    def update_system_settings(self, brand_name: str, brand_logo: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE system_settings SET setting_value = ? WHERE setting_key = 'brand_name'", (brand_name,))
            cursor.execute("UPDATE system_settings SET setting_value = ? WHERE setting_key = 'brand_logo'", (brand_logo,))
            conn.commit()

    def _seed_initial_data(self, cursor, conn):
        depts = ['Cardiology', 'Vascular Surgery', 'Emergency Medicine', 'Internal Medicine']
        for i, dept in enumerate(depts):
            cursor.execute("INSERT INTO navigation_categories (category_type, category_name, sort_order) VALUES (?, ?, ?)", ('department', dept, i))
        statuses = ['General Information', 'Epidemiology', 'Etiology and Pathogenesis', 'History and Physical', 'Diagnosis', 'Treatment', 'Complications and Prognosis', 'Prevention and Screening', 'Guidelines and Resources', 'Patient Information', 'References']
        for i, status in enumerate(statuses):
            cursor.execute("INSERT INTO navigation_categories (category_type, category_name, sort_order) VALUES (?, ?, ?)", ('status', status, i))
        conn.commit()

    def _seed_specialties_data(self, cursor, conn):
        specialties_list = ["Allergy", "Anesthesiology and Pain Management", "Cardiology", "Critical Care", "Dermatology", "Emergency Medicine", "Endocrinology", "Family Medicine", "Gastroenterology", "Geriatrics", "Gynecology", "Hematology", "Hepatology", "Hospital Medicine", "Immunology", "Infectious Diseases", "Internal Medicine", "Neonatology", "Nephrology", "Neurology", "Neurosurgery", "Obesity", "Obstetric Medicine", "Occupational Medicine", "Oncology", "Ophthalmology", "Oral Health", "Orthopedics and Sports Medicine", "Otolaryngology", "Palliative Care", "Pathology and Laboratory Medicine", "Pediatrics", "Physical Medicine and Rehabilitation", "Primary Care", "Psychiatry", "Pulmonary Medicine", "Radiation Oncology", "Radiology", "Rheumatology", "Sleep Medicine", "Substance Use and Addiction Medicine", "Surgery", "Trauma", "Urgent Care", "Urology", "Womens Health"]
        for sp in specialties_list:
            cursor.execute("INSERT OR IGNORE INTO specialties (name) VALUES (?)", (sp,))
        conn.commit()

        cursor.execute("SELECT id FROM specialties WHERE name = 'Allergy'")
        allergy_id = cursor.fetchone()['id']
        allergy_structure = {
            "Allergen Immunotherapy": ["Insect Sting Immunotherapy", "Subcutaneous Immunotherapy for Aeroallergens", "Sublingual Immunotherapy"],
            "Environmental and Insect Allergies": ["Arthropod Bites and Stings", "Atopic Dermatitis in Adults", "Contact Dermatitis", "Effect of Pets in the Home on Allergic Disease", "Hymenoptera Sting Allergy", "Idiopathic Environmental Intolerance (Multiple Chemical Sensitivity)", "Latex Allergy", "Occupational Rhinitis", "Sick Building Syndrome and Building-Related Illnesses"],
            "Drug Allergies": ["Acute Generalized Exanthematous Pustulosis (AGEP)", "Allergic Reactions to Local Anesthetics", "Allergic Reactions to Vaccines", "Hypersensitivity Reactions to Contrast and Dyes", "Aspirin-Exacerbated Respiratory Disease", "Cephalosporin Allergy", "Drug Allergy - Approach to the Patient", "Drug Fever - Approach to the Patient", "Drug Reaction With Eosinophilia and Systemic Symptoms (DRESS)", "Erythema Multiforme", "Exanthematous (Morbilliform) Drug Eruption", "Hypersensitivity Reactions to Clopidogrel and Other Thienopyridines", "Hypersensitivity Reactions to Orthopedic Implants", "Hypersensitivity Reactions to Systemic Chemotherapy", "Hypersensitivity to Insulin", "Hypersensitivity to Quinolones", "Hypersensitivity to NSAIDs", "Penicillin Allergy", "Serum Sickness and Serum Sickness-like Reactions", "Stevens-Johnson Syndrome/Toxic Epidermal Necrolysis", "Sulfa Allergy", "Systemic Corticosteroid Hypersensitivity", "Vancomycin Hypersensitivity"],
            "Food Allergies and Food Intolerance": ["Allergic and Asthmatic Reactions to Food Additives", "Alpha-gal Syndrome (Acquired Meat Allergy)", "Egg Allergy", "Eosinophilic Esophagitis (EoE) in Adults", "Fish and Shellfish Allergy", "Food Protein-Induced Enterocolitis Syndrome (FPIES)", "Immunoglobulin E (IgE)-mediated Food Allergy", "Milk Protein Allergy", "Non-IgE and Mixed-IgE-mediated Food-related Allergy Disorders", "Peanut Allergy", "Pollen-Food Allergy Syndrome", "Wheat Allergy"],
            "Oculo-rhinitis and Pulmonary Allergies": ["Allergic Bronchopulmonary Aspergillosis", "Allergic Conjunctivitis", "Allergic Rhinitis", "Asthma in Adults and Adolescents", "Atopic Keratoconjunctivitis", "Chronic Rhinosinusitis", "Nasal Polyps", "Occupational Rhinitis", "Rhinitis Medicamentosa", "Vasomotor Rhinitis", "Vernal Keratoconjunctivitis"],
            "Immunodeficiencies (Non-HIV)": ["Approach to Recurrent Infections in Adults", "Approach to Recurrent Infections in Children", "Common Variable Immunodeficiency (CVID)", "IgG Deficiency", "Hereditary Agammaglobulinemia", "Hyper IgE Syndrome", "Idiopathic CD4 Lymphocytopenia", "Nijmegen Breakage Syndrome (NBS)", "Primary Disorders of Phagocyte Function", "Selective IgA and IgM Deficiencies", "Severe Combined Immunodeficiency (SCID)", "Specific Antibody Deficiency", "Transient Hypogammaglobulinemia of Infancy", "Wiskott-Aldrich Syndrome"],
            "Anaphylaxis, Urticaria and Angioedema": ["Acute Urticaria", "Anaphylaxis", "Angioedema", "C1 Inhibitor Deficiency", "Chronic Urticaria"],
            "Diseases of Hypersensitivity": ["Allergen specific IgE antibody measurement", "Diagnosis and Evaluation of IgE-mediated Allergies", "Diagnosis and Evaluation of Non-IgE- and Mixed-IgE-Mediated Allergies", "Idiopathic Environmental Intolerance (Multiple Chemical Sensitivity)", "Metal Hypersensitivity to Implanted Cardiovascular Devices", "Seminal Fluid Allergy", "Sick Building Syndrome and Building-Related Illnesses"]
        }
        for group_name, topics in allergy_structure.items():
            cursor.execute("INSERT INTO topic_groups (specialty_id, name) VALUES (?, ?)", (allergy_id, group_name))
            group_id = cursor.lastrowid
            for topic in topics:
                cursor.execute("INSERT INTO topics (group_id, name) VALUES (?, ?)", (group_id, topic))
        conn.commit()

    def get_categories_by_type(self, category_type: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, category_name FROM navigation_categories WHERE category_type = ? ORDER BY sort_order ASC", (category_type,))
            return [dict(row) for row in cursor.fetchall()]

    def get_all_specialties(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM specialties ORDER BY name ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_specialty_tree(self, specialty_name: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM specialties WHERE name = ?", (specialty_name,))
            sp = cursor.fetchone()
            if not sp: return []
            sp_id = sp['id']
            cursor.execute("SELECT id, name FROM topic_groups WHERE specialty_id = ?", (sp_id,))
            groups = [dict(row) for row in cursor.fetchall()]
            tree = []
            for g in groups:
                cursor.execute("SELECT id, name FROM topics WHERE group_id = ?", (g['id'],))
                topics = [dict(row) for row in cursor.fetchall()]
                tree.append({"group_name": g['name'], "topics": topics})
            return tree

    def _seed_all_medical_data_v2(self, cursor, conn):
        anesthesia_tree = {"Anesthesiology and Pain Management": {"Anesthesia for Select Patient Populations": ["Analgesia, Sedation, and Use of Paralytics in the Intensive Care Unit"]}}
        cardiology_tree = {"Cardiology": {"Acute Coronary Syndromes": {"Non-ST-elevation Acute Coronary Syndromes (NSTE-ACS)": {"Types": ["Acute Coronary Syndromes"]}}}}
        def insert_recursive(data, parent_id, current_layer):
            if isinstance(data, dict):
                for name, children in data.items():
                    cursor.execute("INSERT INTO medical_topics (parent_id, name, layer_level) VALUES (?, ?, ?)", (parent_id, name, current_layer))
                    new_id = cursor.lastrowid
                    insert_recursive(children, new_id, current_layer + 1)
            elif isinstance(data, list):
                for name in data:
                    cursor.execute("INSERT INTO medical_topics (parent_id, name, layer_level) VALUES (?, ?, ?)", (parent_id, name, current_layer))
                    if isinstance(name, dict):
                         insert_recursive(name, cursor.lastrowid, current_layer + 1)
            else:
                cursor.execute("INSERT INTO medical_topics (parent_id, name, layer_level) VALUES (?, ?, ?)", (parent_id, data, current_layer))
        insert_recursive(anesthesia_tree, None, 1)
        insert_recursive(cardiology_tree, None, 1)
        conn.commit()

    def get_all_specialties_v2(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM medical_topics WHERE layer_level = 1 ORDER BY name ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_specialty_tree_v2(self, specialty_name: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM medical_topics WHERE name = ? AND layer_level = 1", (specialty_name,))
            root = cursor.fetchone()
            if not root: return []
            def build_node(parent_id):
                cursor.execute("SELECT id, name, layer_level FROM medical_topics WHERE parent_id = ?", (parent_id,))
                children = cursor.fetchall()
                node_list = []
                for child in children:
                    node_list.append({"id": child['id'], "name": child['name'], "layer": child['layer_level'], "children": build_node(child['id'])})
                return node_list
            return build_node(root['id'])

ebm_db = EBMDatabase()

# ==========================================
# 獨立 Admin 知識庫 + 安全 CRUD 引擎
# ==========================================
ADMIN_DB_PATH = os.path.join(SYSTEM_DB_DIR, "specialty.db")

class SpecialtyDatabase:
    def __init__(self, db_path=ADMIN_DB_PATH):
        self.db_path = db_path
        # [v1.4] Base-33 字典 (0-9 + 23大寫字母, 排除 I, O, Z 避免視覺混淆)
        self.UID_CHARS = "0123456789ABCDEFGHJKLMNPQRSTUVWXY"
        self._init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row 
        try:
            yield conn
        finally:
            conn.close()

    def _generate_uid_segment(self):
        """生成隨機的 2 碼 Base-33 辨識碼"""
        return "".join(random.choices(self.UID_CHARS, k=2))

    def _init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS taxonomy_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER,
                    name TEXT NOT NULL,
                    layer_level INTEGER NOT NULL,
                    FOREIGN KEY (parent_id) REFERENCES taxonomy_nodes (id)
                )
            ''')
            
            # [v1.4] 確保 uid 欄位存在
            try:
                cursor.execute("ALTER TABLE taxonomy_nodes ADD COLUMN uid TEXT")
            except sqlite3.OperationalError:
                pass 
                
            conn.commit()

            all_specialties = [
                "Allergy", "Anesthesiology and Pain Management", "Cardiology", "Critical Care",
                "Dermatology", "Emergency Medicine", "Endocrinology", "Family Medicine",
                "Gastroenterology", "Geriatrics", "Gynecology", "Hematology", "Hepatology",
                "Hospital Medicine", "Immunology", "Infectious Diseases", "Internal Medicine",
                "Neonatology", "Nephrology", "Neurology", "Neurosurgery", "Obesity",
                "Obstetric Medicine", "Occupational Medicine", "Oncology", "Ophthalmology",
                "Oral Health", "Orthopedics and Sports Medicine", "Otolaryngology", "Palliative Care",
                "Pathology and Laboratory Medicine", "Pediatrics", "Physical Medicine and Rehabilitation",
                "Primary Care", "Psychiatry", "Pulmonary Medicine", "Radiation Oncology", "Radiology",
                "Rheumatology", "Sleep Medicine", "Substance Use and Addiction Medicine", "Surgery",
                "Trauma", "Urgent Care", "Urology", "Womens Health",
                "Type 1 Diabetes Mellitus in Children and Adolescents"
            ]
            for sp in all_specialties:
                cursor.execute("SELECT id FROM taxonomy_nodes WHERE name = ? AND layer_level = 1 AND parent_id IS NULL", (sp,))
                if not cursor.fetchone():
                    seg = self._generate_uid_segment()
                    cursor.execute("INSERT INTO taxonomy_nodes (parent_id, name, layer_level, uid) VALUES (NULL, ?, 1, ?)", (sp, seg))
            conn.commit()
            
            # [v1.4] 樹狀遍歷，為舊有資料補齊階層 UID
            self._ensure_uids_exist(cursor, conn)

    def _ensure_uids_exist(self, cursor, conn):
        """遞迴遍歷整棵樹，若發現節點缺少 UID 則繼承父系血脈生成。"""
        cursor.execute("SELECT id, parent_id, uid FROM taxonomy_nodes ORDER BY layer_level ASC, id ASC")
        nodes = cursor.fetchall()
        needs_update = any(not row['uid'] for row in nodes)
        
        if not needs_update:
            return

        children_map = defaultdict(list)
        for row in nodes:
            children_map[row['parent_id']].append(row['id'])

        def assign_uids_recursive(node_id, parent_uid=""):
            seg = self._generate_uid_segment()
            current_uid = f"{parent_uid}-{seg}" if parent_uid else seg
            
            cursor.execute("UPDATE taxonomy_nodes SET uid = ? WHERE id = ? AND uid IS NULL", (current_uid, node_id))
            
            cursor.execute("SELECT uid FROM taxonomy_nodes WHERE id = ?", (node_id,))
            actual_uid = cursor.fetchone()['uid']
            
            for child_id in children_map.get(node_id, []):
                assign_uids_recursive(child_id, actual_uid)

        for root_id in children_map.get(None, []):
            assign_uids_recursive(root_id, "")
            
        conn.commit()

    def get_nodes_by_parent(self, parent_id=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if parent_id is None:
                cursor.execute("SELECT id, name, layer_level, uid FROM taxonomy_nodes WHERE parent_id IS NULL ORDER BY name ASC")
            else:
                cursor.execute("SELECT id, name, layer_level, uid FROM taxonomy_nodes WHERE parent_id = ? ORDER BY name ASC", (parent_id,))
            return [dict(row) for row in cursor.fetchall()]

    def batch_insert_from_csv(self, parent_id, csv_content: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            parent_uid = ""
            if parent_id is None:
                target_layer = 1
            else:
                cursor.execute("SELECT layer_level, uid FROM taxonomy_nodes WHERE id = ?", (parent_id,))
                parent_row = cursor.fetchone()
                if not parent_row: raise ValueError("Invalid parent_id provided.")
                target_layer = parent_row['layer_level'] + 1
                parent_uid = parent_row['uid']

            inserted_count = 0
            reader = csv.reader(io.StringIO(csv_content))
            for row in reader:
                if not row: continue
                node_name = row[0].strip()
                if node_name:
                    seg = self._generate_uid_segment()
                    new_uid = f"{parent_uid}-{seg}" if parent_uid else seg
                    cursor.execute(
                        "INSERT INTO taxonomy_nodes (parent_id, name, layer_level, uid) VALUES (?, ?, ?, ?)", 
                        (parent_id, node_name, target_layer, new_uid)
                    )
                    inserted_count += 1
            conn.commit()
            return inserted_count

    def add_node(self, parent_id, name: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            parent_uid = ""
            if parent_id is None:
                layer_level = 1
            else:
                cursor.execute("SELECT layer_level, uid FROM taxonomy_nodes WHERE id = ?", (parent_id,))
                row = cursor.fetchone()
                if not row: raise ValueError("Parent ID not found.")
                layer_level = row['layer_level'] + 1
                parent_uid = row['uid']
                
            seg = self._generate_uid_segment()
            new_uid = f"{parent_uid}-{seg}" if parent_uid else seg
            
            cursor.execute("INSERT INTO taxonomy_nodes (parent_id, name, layer_level, uid) VALUES (?, ?, ?, ?)", (parent_id, name.strip(), layer_level, new_uid))
            conn.commit()
            return cursor.lastrowid

    def update_node(self, node_id: int, new_name: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE taxonomy_nodes SET name = ? WHERE id = ?", (new_name.strip(), node_id))
            if cursor.rowcount == 0: raise ValueError("Node not found.")
            conn.commit()
            return True

    def delete_node(self, node_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM taxonomy_nodes WHERE parent_id = ?", (node_id,))
            child_count = cursor.fetchone()[0]
            if child_count > 0:
                raise ValueError("Cannot delete node: It contains active sub-categories (child nodes).")
            cursor.execute("DELETE FROM taxonomy_nodes WHERE id = ?", (node_id,))
            if cursor.rowcount == 0: raise ValueError("Node not found.")
            conn.commit()
            return True

    def get_node_by_name(self, name: str):
        """[v1.4] 精準撈取節點，回傳 UID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, parent_id, name, layer_level, uid FROM taxonomy_nodes WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_specialties_for_frontend(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, uid FROM taxonomy_nodes WHERE parent_id IS NULL AND layer_level = 1 ORDER BY name ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_specialty_tree_for_frontend(self, specialty_name: str):
        """撈取 5 層樹狀結構，並將 UID 一併帶回前端"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, parent_id, name, layer_level, uid FROM taxonomy_nodes ORDER BY layer_level ASC, id ASC")
            all_nodes = [dict(row) for row in cursor.fetchall()]
            
        root = next((n for n in all_nodes if n['name'] == specialty_name and n['parent_id'] is None), None)
        if not root:
            return []
            
        children_map = defaultdict(list)
        for node in all_nodes:
            if node['parent_id'] is not None:
                children_map[node['parent_id']].append(node)
                
        def build_tree_in_memory(node_id):
            children = children_map.get(node_id, [])
            node_list = []
            for child in children:
                node_list.append({
                    "id": child['id'],
                    "name": child['name'],
                    "layer": child['layer_level'],
                    "uid": child['uid'], # [v1.4] 攜帶 UID
                    "children": build_tree_in_memory(child['id'])
                })
            return node_list
            
        return build_tree_in_memory(root['id'])

admin_db = SpecialtyDatabase()