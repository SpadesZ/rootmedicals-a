-- 模組定位: llmebm condition hierarchy 的版本化 SQL template seed。
-- 主要責任: 保存跨疾病共用的 21 個 stable node_key、層級、順序與 allowed blocks。
-- 呼叫來源: SidebarDatabase 啟動時的 checksum-locked migration runner，只執行一次。
-- 輸入契約: 已建立 sidebar_template_nodes 與 schema_migrations 的 SQLite schema。
-- 輸出契約: condition.v1 template 可冪等 materialize；不改既有 topic rows。
-- 安全邊界: 僅含 llmebm 自有 heading schema，不含第三方文章、HTML、圖片或登入資料。
-- 維護提醒: 已套用 migration 禁止改檔；變更 taxonomy 必須新增下一版 SQL。
-- ----------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sidebar_template_nodes (
    template_key TEXT NOT NULL, node_key TEXT NOT NULL, parent_node_key TEXT, name TEXT NOT NULL,
    layer_level INTEGER NOT NULL CHECK(layer_level BETWEEN 1 AND 6), sort_order INTEGER NOT NULL DEFAULT 0,
    content_target INTEGER NOT NULL DEFAULT 1 CHECK(content_target IN (0, 1)), allowed_blocks_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('draft', 'published', 'retired')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(template_key, node_key)
);

INSERT OR IGNORE INTO sidebar_template_nodes
    (template_key, node_key, parent_node_key, name, layer_level, sort_order, content_target, allowed_blocks_json, status)
VALUES
    ('condition.v1', 'u1', NULL, 'Overview and Recommendations', 1, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u1-1', 'u1', 'Evaluation', 2, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u1-2', 'u1', 'Management', 2, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u2', NULL, 'Background Information', 1, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u2-1', 'u2', 'Description', 2, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u2-2', 'u2', 'Epidemiology', 2, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u2-2-1', 'u2-2', 'Incidence/Prevalence', 3, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u3', NULL, 'Diagnosis', 1, 2, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u3-1', 'u3', 'Making the Diagnosis', 2, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u3-2', 'u3', 'Testing Overview', 2, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u3-2-1', 'u3-2', 'Blood Tests', 3, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u3-2-2', 'u3-2', 'Imaging', 3, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u4', NULL, 'Management', 1, 3, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u4-1', 'u4', 'Management Overview', 2, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u4-2', 'u4', 'Medications', 2, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u4-2-1', 'u4-2', 'First-line therapies', 3, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u4-2-2', 'u4-2', 'Alternative therapies', 3, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u5', NULL, 'Guidelines and Resources', 1, 4, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u5-1', 'u5', 'International Guidelines', 2, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u5-2', 'u5', 'United States Guidelines', 2, 1, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published'),
    ('condition.v1', 'u5-2-1', 'u5-2', 'Professional Society Guidelines', 3, 0, 1, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published');

