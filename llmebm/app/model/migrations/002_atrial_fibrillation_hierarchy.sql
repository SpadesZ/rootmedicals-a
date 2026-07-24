-- 模組定位: llmebm Atrial Fibrillation 121-slot hierarchy 的版本化 SQL migration。
-- 主要責任: 將人工核准的 heading、parent、level、order 寫入自有 sidebar_nodes。
-- 呼叫來源: SidebarDatabase checksum-locked migration runner，只對未套用版本執行一次。
-- 輸入契約: 既有 stable topic_uid/node_key；父節點必須在子節點前一層可解析。
-- 輸出契約: 121 個 published universal rows；既有 u* slot_id 與 row id 不變。
-- 安全邊界: 只保存醫學 heading taxonomy，不含 DynaMed 內文、markup、圖片或品牌資產。
-- 維護提醒: 禁止覆寫本檔；後續 heading 調整以新 migration 精準更新單一 node_key。
-- ----------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sidebar_topic_seed_nodes (
    topic_uid TEXT NOT NULL,
    node_key TEXT NOT NULL,
    parent_node_key TEXT,
    name TEXT NOT NULL,
    layer_level INTEGER NOT NULL CHECK(layer_level BETWEEN 1 AND 6),
    sort_order INTEGER NOT NULL DEFAULT 0,
    content_target INTEGER NOT NULL DEFAULT 1 CHECK(content_target IN (0, 1)),
    allowed_blocks_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('draft', 'published', 'retired')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(topic_uid, node_key)
);

DROP TABLE IF EXISTS temp._migration_af_nodes;
CREATE TEMP TABLE _migration_af_nodes (node_key TEXT PRIMARY KEY, parent_node_key TEXT, name TEXT NOT NULL, layer_level INTEGER NOT NULL, sort_order INTEGER NOT NULL, content_target INTEGER NOT NULL);
INSERT INTO _migration_af_nodes (node_key, parent_node_key, name, layer_level, sort_order, content_target) VALUES
    ('u1', NULL, 'Overview and Recommendations', 1, 0, 1),
    ('u1-1', 'u1', 'Evaluation', 2, 0, 1),
    ('u1-2', 'u1', 'Management', 2, 1, 1),
    ('u2', NULL, 'Background Information', 1, 1, 1),
    ('u2-1', 'u2', 'Description', 2, 0, 1),
    ('u2-2', 'u2', 'Epidemiology', 2, 3, 1),
    ('u2-2-1', 'u2-2', 'Incidence/Prevalence', 3, 0, 1),
    ('u3', NULL, 'Diagnosis', 1, 3, 1),
    ('u3-1', 'u3', 'Making the Diagnosis', 2, 0, 1),
    ('u3-2', 'u3', 'Testing Overview', 2, 2, 1),
    ('u3-2-1', 'u3', 'Blood Tests', 2, 4, 1),
    ('u3-2-2', 'u3', 'Transthoracic Echocardiogram (TTE)', 2, 6, 1),
    ('u4', NULL, 'Management', 1, 4, 1),
    ('u4-1', 'u4', 'Management Overview', 2, 0, 1),
    ('u4-2', 'u4', 'Treatment Setting', 2, 1, 1),
    ('u4-2-1', 'u4', 'Rate Control', 2, 2, 1),
    ('u4-2-2', 'u4', 'Cardioversion', 2, 3, 1),
    ('u5', NULL, 'Guidelines and Resources', 1, 9, 1),
    ('u5-1', 'u5', 'Guidelines', 2, 0, 1),
    ('u5-2', 'u5-1', 'International Guidelines', 3, 0, 1),
    ('u5-2-1', 'u5-1', 'United States Guidelines', 3, 1, 1),
    ('af-bg-also-called', 'u2', 'Also Called', 2, 1, 1),
    ('af-bg-definitions', 'u2', 'Definitions', 2, 2, 1),
    ('af-bg-def-acc-stage', 'af-bg-definitions', 'American College of Cardiology/American Heart Association/American College of Clinical Pharmacy/Heart Rhythm Society (ACC/AHA/ACCP/HRS) Staging for Atrial Fibrillation', 3, 0, 1),
    ('af-bg-def-ccs-saf', 'af-bg-definitions', 'Canadian Cardiovascular Society (CCS) Severity of Atrial Fibrillation (SAF) Score', 3, 1, 1),
    ('af-bg-def-ehra', 'af-bg-definitions', 'European Heart Rhythm Association (EHRA) Class', 3, 2, 1),
    ('af-bg-risk-factors', 'u2-2', 'Risk Factors', 3, 1, 1),
    ('af-bg-risk-cardiac', 'af-bg-risk-factors', 'Cardiac Abnormalities', 4, 0, 1),
    ('af-bg-risk-thyroid', 'af-bg-risk-factors', 'Subclinical Hyperthyroidism', 4, 1, 1),
    ('af-bg-risk-alcohol', 'af-bg-risk-factors', 'Alcohol Consumption', 4, 2, 1),
    ('af-bg-risk-obesity', 'af-bg-risk-factors', 'Obesity', 4, 3, 1),
    ('af-bg-risk-medications', 'af-bg-risk-factors', 'Medications Associated With Atrial Fibrillation', 4, 4, 1),
    ('af-bg-risk-bp', 'af-bg-risk-factors', 'Abnormal Blood Pressure', 4, 5, 1),
    ('af-bg-risk-genetic', 'af-bg-risk-factors', 'Family History and Genetic Factors', 4, 6, 1),
    ('af-bg-risk-sleep', 'af-bg-risk-factors', 'Sleep-Disordered Breathing', 4, 7, 1),
    ('af-bg-risk-comorbid', 'af-bg-risk-factors', 'Other Medical Comorbidities', 4, 8, 1),
    ('af-bg-risk-lifestyle', 'af-bg-risk-factors', 'Lifestyle-Related Factors', 4, 9, 1),
    ('af-bg-risk-mental', 'af-bg-risk-factors', 'Mental Health Conditions', 4, 10, 1),
    ('af-bg-risk-prediction', 'u2-2', 'Risk Prediction', 3, 2, 1),
    ('af-bg-risk-not-associated', 'u2-2', 'Factors Not Associated With Increased Risk', 3, 3, 1),
    ('af-bg-associated', 'u2-2', 'Associated Conditions', 3, 4, 1),
    ('af-bg-etiology', 'u2', 'Etiology and Pathogenesis', 2, 4, 1),
    ('af-bg-causes', 'af-bg-etiology', 'Causes', 3, 0, 1),
    ('af-bg-pathogenesis-overview', 'af-bg-etiology', 'Pathogenesis Overview', 3, 1, 1),
    ('af-bg-trigger-substrate', 'af-bg-etiology', 'Trigger and Substrate Pathogenesis Theory', 3, 2, 1),
    ('af-bg-genetic-associations', 'af-bg-etiology', 'Genetic Associations', 3, 3, 1),
    ('af-bg-postoperative-pathogenesis', 'af-bg-etiology', 'Pathogenesis of Postoperative Atrial Fibrillation and Late Recurrence After Catheter Ablation', 3, 4, 1),
    ('af-hp', NULL, 'History and Physical', 1, 2, 1),
    ('af-hp-history', 'af-hp', 'History', 2, 0, 1),
    ('af-hp-cc', 'af-hp-history', 'Chief Concern (CC)', 3, 0, 1),
    ('af-hp-hpi', 'af-hp-history', 'History of Present Illness (HPI)', 3, 1, 1),
    ('af-hp-pmh', 'af-hp-history', 'Past Medical History (PMH)', 3, 2, 1),
    ('af-hp-fh', 'af-hp-history', 'Family History (FH)', 3, 3, 1),
    ('af-hp-sh', 'af-hp-history', 'Social History (SH)', 3, 4, 1),
    ('af-hp-physical', 'af-hp', 'Physical', 2, 1, 1),
    ('af-hp-general', 'af-hp-physical', 'General Physical', 3, 0, 1),
    ('af-hp-neck', 'af-hp-physical', 'Neck', 3, 1, 1),
    ('af-hp-cardiac', 'af-hp-physical', 'Cardiac', 3, 2, 1),
    ('af-hp-lungs', 'af-hp-physical', 'Lungs', 3, 3, 1),
    ('af-hp-extremities', 'af-hp-physical', 'Extremities', 3, 4, 1),
    ('af-dx-differential', 'u3', 'Differential Diagnosis', 2, 1, 1),
    ('af-dx-evaluative-monitoring', 'u3', 'General Evaluative and Rhythm Monitoring Recommendations', 2, 3, 1),
    ('af-dx-ecg', 'u3', 'Electrocardiography (ECG)', 2, 5, 1),
    ('af-dx-ecg-indications', 'af-dx-ecg', 'Indications for ECG and ECG Images', 3, 0, 1),
    ('af-dx-ecg-detection', 'af-dx-ecg', 'Evidence for Electrocardiography (ECG) Detection of Atrial Fibrillation', 3, 1, 1),
    ('af-dx-ecg-software', 'af-dx-ecg', 'Interpretive Software for ECG', 3, 2, 1),
    ('af-dx-tee', 'u3', 'Transesophageal Echocardiogram (TEE)', 2, 7, 1),
    ('af-dx-intracardiac-echo', 'u3', 'Intracardiac Echocardiography', 2, 8, 1),
    ('af-dx-ct', 'u3', 'Computed Tomography', 2, 9, 1),
    ('af-dx-mobile', 'u3', 'Mobile Technology', 2, 10, 1),
    ('af-mgmt-antiarrhythmic', 'u4', 'Antiarrhythmic Drugs for Rhythm Control', 2, 4, 1),
    ('af-mgmt-nonantiarrhythmic', 'u4', 'Nonantiarrhythmic Drugs with Antiarrhythmic Effects', 2, 5, 1),
    ('af-mgmt-beta-blockers', 'af-mgmt-nonantiarrhythmic', 'Beta Blockers', 3, 0, 1),
    ('af-mgmt-ace-arb', 'af-mgmt-nonantiarrhythmic', 'ACE inhibitors and ARBs', 3, 1, 1),
    ('af-mgmt-statins', 'af-mgmt-nonantiarrhythmic', 'Statins', 3, 2, 1),
    ('af-mgmt-colchicine', 'af-mgmt-nonantiarrhythmic', 'Colchicine', 3, 3, 1),
    ('af-mgmt-omega3', 'af-mgmt-nonantiarrhythmic', 'Omega-3 Fatty Acids', 3, 4, 1),
    ('af-mgmt-ablation-rhythm', 'u4', 'Ablation Therapy for Rhythm Control', 2, 6, 1),
    ('af-mgmt-rate-vs-rhythm', 'u4', 'Rate vs. Rhythm Control', 2, 7, 1),
    ('af-mgmt-thromboembolic', 'u4', 'Thromboembolic Prophylaxis', 2, 8, 1),
    ('af-mgmt-abc', 'u4', 'Atrial fibrillation Better Care (ABC) Pathway', 2, 9, 1),
    ('af-mgmt-risk-factors', 'u4', 'Management of Modifiable Risk Factors', 2, 10, 1),
    ('af-mgmt-risk-recommendations', 'af-mgmt-risk-factors', 'Recommendations From Professional Organizations', 3, 0, 1),
    ('af-mgmt-risk-weight', 'af-mgmt-risk-factors', 'Efficacy of Weight Loss and Physical Activity', 3, 1, 1),
    ('af-mgmt-risk-alcohol', 'af-mgmt-risk-factors', 'Efficacy of Reduced Alcohol Consumption', 3, 2, 1),
    ('af-mgmt-risk-sleep', 'af-mgmt-risk-factors', 'Efficacy of Management of Sleep-Disordered Breathing', 3, 3, 1),
    ('af-mgmt-procedures', 'u4', 'Surgery and Procedures', 2, 11, 1),
    ('af-mgmt-procedure-ablation', 'af-mgmt-procedures', 'Ablation Therapy', 3, 0, 1),
    ('af-mgmt-procedure-pacing', 'af-mgmt-procedures', 'Pacing', 3, 1, 1),
    ('af-mgmt-procedure-laac', 'af-mgmt-procedures', 'Left Atrial Appendage Closure', 3, 2, 1),
    ('af-mgmt-severe-mental-illness', 'u4', 'Management of Atrial Fibrillation in Patients with Severe Mental Illness', 2, 12, 1),
    ('af-mgmt-follow-up', 'u4', 'Follow-Up', 2, 13, 1),
    ('af-burden', NULL, 'Atrial Fibrillation (AF) Burden', 1, 5, 1),
    ('af-burden-definitions', 'af-burden', 'Definitions of AF Burden', 2, 0, 1),
    ('af-burden-ablation', 'af-burden', 'Catheter Ablation for AF Burden', 2, 1, 1),
    ('af-burden-prognosis', 'af-burden', 'Prognosis Based on AF Burden', 2, 2, 1),
    ('af-complications-prognosis', NULL, 'Complications and Prognosis', 1, 6, 1),
    ('af-complications', 'af-complications-prognosis', 'Complications', 2, 0, 1),
    ('af-prognosis', 'af-complications-prognosis', 'Prognosis', 2, 1, 1),
    ('af-prognosis-recurrence', 'af-prognosis', 'Recurrence Risk', 3, 0, 1),
    ('af-prognosis-stroke', 'af-prognosis', 'Embolic Stroke and Thromboembolism', 3, 1, 1),
    ('af-prognosis-cvd', 'af-prognosis', 'Cardiovascular Disease', 3, 2, 1),
    ('af-prognosis-mortality', 'af-prognosis', 'Mortality Risk', 3, 3, 1),
    ('af-prevention-screening', NULL, 'Prevention and Screening', 1, 7, 1),
    ('af-prevention', 'af-prevention-screening', 'Prevention', 2, 0, 1),
    ('af-screening', 'af-prevention-screening', 'Screening', 2, 1, 1),
    ('af-quality', NULL, 'Quality Improvement', 1, 8, 1),
    ('af-quality-acc-aha', 'af-quality', 'American College of Cardiology (ACC)/American Heart Association (AHA) Performance Measures', 2, 0, 1),
    ('af-quality-qof', 'af-quality', 'Quality and Outcomes Framework Indicators', 2, 1, 1),
    ('af-guidelines-uk', 'u5-1', 'United Kingdom Guidelines', 3, 2, 1),
    ('af-guidelines-canada', 'u5-1', 'Canadian Guidelines', 3, 3, 1),
    ('af-guidelines-europe', 'u5-1', 'European Guidelines', 3, 4, 1),
    ('af-guidelines-asia', 'u5-1', 'Asian Guidelines', 3, 5, 1),
    ('af-guidelines-au-nz', 'u5-1', 'Australian and New Zealand Guidelines', 3, 6, 1),
    ('af-review-articles', 'u5', 'Review Articles', 2, 1, 1),
    ('af-patient-information', NULL, 'Patient Information', 1, 10, 1),
    ('af-references', NULL, 'References', 1, 11, 1),
    ('af-references-general', 'af-references', 'General References Used', 2, 0, 1),
    ('af-references-grading', 'af-references', 'Recommendation Grading Systems Used', 2, 1, 1),
    ('af-references-editorial', 'af-references', 'DynaMed Editorial Process', 2, 2, 1),
    ('af-references-acknowledgements', 'af-references', 'Special Acknowledgements', 2, 3, 1);

INSERT OR IGNORE INTO sidebar_topic_seed_nodes
    (topic_uid, node_key, parent_node_key, name, layer_level, sort_order,
     content_target, allowed_blocks_json, status)
SELECT 'topic-e55a50c1a5a4445c', node_key, parent_node_key, name, layer_level,
       sort_order, content_target,
       '["summary","recommendations","bullets","evidence_note","table","warning"]',
       'published'
FROM _migration_af_nodes;

INSERT OR IGNORE INTO sidebar_nodes (topic_name, topic_uid, parent_id, node_key, source, name, layer_level, sort_order, content_target, allowed_blocks_json, status, created_at, updated_at)
SELECT 'atrial-fibrillation', 'topic-e55a50c1a5a4445c', parent.id, seed.node_key, 'universal', seed.name, seed.layer_level, seed.sort_order, seed.content_target, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM _migration_af_nodes AS seed LEFT JOIN sidebar_nodes AS parent ON parent.topic_uid = 'topic-e55a50c1a5a4445c' AND parent.source = 'universal' AND parent.node_key = seed.parent_node_key
WHERE seed.layer_level = 1
  AND EXISTS (
      SELECT 1 FROM sidebar_nodes AS existing
      WHERE existing.topic_uid = 'topic-e55a50c1a5a4445c'
  );

INSERT OR IGNORE INTO sidebar_nodes (topic_name, topic_uid, parent_id, node_key, source, name, layer_level, sort_order, content_target, allowed_blocks_json, status, created_at, updated_at)
SELECT 'atrial-fibrillation', 'topic-e55a50c1a5a4445c', parent.id, seed.node_key, 'universal', seed.name, seed.layer_level, seed.sort_order, seed.content_target, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM _migration_af_nodes AS seed JOIN sidebar_nodes AS parent ON parent.topic_uid = 'topic-e55a50c1a5a4445c' AND parent.source = 'universal' AND parent.node_key = seed.parent_node_key
WHERE seed.layer_level = 2;

INSERT OR IGNORE INTO sidebar_nodes (topic_name, topic_uid, parent_id, node_key, source, name, layer_level, sort_order, content_target, allowed_blocks_json, status, created_at, updated_at)
SELECT 'atrial-fibrillation', 'topic-e55a50c1a5a4445c', parent.id, seed.node_key, 'universal', seed.name, seed.layer_level, seed.sort_order, seed.content_target, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM _migration_af_nodes AS seed JOIN sidebar_nodes AS parent ON parent.topic_uid = 'topic-e55a50c1a5a4445c' AND parent.source = 'universal' AND parent.node_key = seed.parent_node_key
WHERE seed.layer_level = 3;

INSERT OR IGNORE INTO sidebar_nodes (topic_name, topic_uid, parent_id, node_key, source, name, layer_level, sort_order, content_target, allowed_blocks_json, status, created_at, updated_at)
SELECT 'atrial-fibrillation', 'topic-e55a50c1a5a4445c', parent.id, seed.node_key, 'universal', seed.name, seed.layer_level, seed.sort_order, seed.content_target, '["summary","recommendations","bullets","evidence_note","table","warning"]', 'published', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM _migration_af_nodes AS seed JOIN sidebar_nodes AS parent ON parent.topic_uid = 'topic-e55a50c1a5a4445c' AND parent.source = 'universal' AND parent.node_key = seed.parent_node_key
WHERE seed.layer_level = 4;

UPDATE sidebar_nodes AS node SET topic_name = 'atrial-fibrillation', parent_id = parent.id, name = seed.name, layer_level = seed.layer_level, sort_order = seed.sort_order, content_target = seed.content_target, allowed_blocks_json = '["summary","recommendations","bullets","evidence_note","table","warning"]', status = 'published', updated_at = CURRENT_TIMESTAMP
FROM _migration_af_nodes AS seed LEFT JOIN sidebar_nodes AS parent ON parent.topic_uid = 'topic-e55a50c1a5a4445c' AND parent.source = 'universal' AND parent.node_key = seed.parent_node_key
WHERE node.topic_uid = 'topic-e55a50c1a5a4445c' AND node.source = 'universal' AND node.node_key = seed.node_key;
DROP TABLE _migration_af_nodes;
