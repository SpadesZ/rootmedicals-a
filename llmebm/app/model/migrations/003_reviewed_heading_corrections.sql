-- 模組定位: llmebm legacy universal heading 的版本化精準修正。
-- 主要責任: 只在舊 heading 逐字命中時，修正四個已人工核准的醫學標題。
-- 呼叫來源: SidebarDatabase checksum-locked migration runner，在 001/002 後只執行一次。
-- 輸入契約: stable node_key 與舊 heading 同時命中；不以 topic 名稱猜測語意。
-- 輸出契約: node_key、parent、order 與 slot_id 不變，只有目標 name 更新。
-- 安全邊界: 不碰 custom rows、不改未命中或使用者後續已編輯的 heading。
-- 維護提醒: 新的語意修正必須新增 migration，不可改寫已套用檔案。
-- ----------------------------------------------------------------------------------------------------
UPDATE sidebar_nodes SET name = 'Testing Overview', updated_at = CURRENT_TIMESTAMP
WHERE source = 'universal' AND node_key = 'u3-2' AND name = 'Testing';

UPDATE sidebar_nodes SET name = 'Blood Tests', updated_at = CURRENT_TIMESTAMP
WHERE source = 'universal' AND node_key = 'u3-2-1' AND name = 'Laboratory Tests';

UPDATE sidebar_nodes SET name = 'Management Overview', updated_at = CURRENT_TIMESTAMP
WHERE source = 'universal' AND node_key = 'u4-1' AND name = 'Treatment Overview';

UPDATE sidebar_nodes SET name = 'Professional Society Guidelines', updated_at = CURRENT_TIMESTAMP
WHERE source = 'universal' AND node_key = 'u5-2-1' AND name = 'ADA Guidelines';
