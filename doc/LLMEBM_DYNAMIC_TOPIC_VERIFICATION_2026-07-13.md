<!--
模組定位: llmebm 動態 Topic Page P1 的永久 evidence-backed 驗收報告。
主要責任: 記錄 Phase A-F、§14、header gate、live browser 與剩餘 blocker。
呼叫來源: README 永久入口、交付審查與後續 P1/P2 決策。
輸入契約: 2026-07-13 本機 source/tests、33300/33301 runtime、SQLite 與 Chrome 實測結果。
輸出契約: 可重跑的命令、artifact/hash、PASS/PARTIAL 判定與禁止過度宣稱的範圍。
安全邊界: 不記錄 API key、cookie、screenshot base64、PHI 或第三方受保護內容。
維護提醒: blocker 修復後新增 dated verification，不覆寫本次 point-in-time 證據。
-->

# llmebm Dynamic Topic Page P1 驗收報告（2026-07-13）

## 結論

本次完成本機 1～4 的實作與 evidence-backed 驗收，包含 legacy smoke、AF 精準 scope 回填、21-slot transaction、affected-only stale/regeneration、browser 與 citation trace。整體仍判定為 **CONDITIONAL**：技術 pipeline 已閉環，但有效內容為 6 ready、10 insufficient、5 stale，不能宣稱 21-slot 臨床內容完整；VM profile 已 fail-closed，仍缺實機內部 service topology，因此尚未完成 live VM 驗收。`_TEMP_LLMEBM_DYNAMIC_TOPIC_IMPLEMENTATION_DRAFT_2026-07-10.md` 不得刪除。

## SQL hierarchy migration follow-up（2026-07-13 23:47 +08:00）

- `sidebar_nodes` 以 additive、idempotent migration 新增 `topic_uid`、`node_key`、`source`、`sort_order`、`content_target`；既有 `parent_id` 繼續表達三層 hierarchy。Universal template 只作首次 seed，寫入後由 SQL 成為讀取來源，啟動不會覆寫後續 heading edit。
- migration 前發現 DB 已有 69 個 Type 1 diabetes legacy hierarchy rows。因舊 schema 沒有 provenance，全部保留既有 row ID、標為 `custom`、`node_key=CAST(id AS TEXT)`；不猜測、不重新分類，69/69 identity check 通過。
- AF 首次讀取後 SQL 有 21 個 `universal` rows，`node_key` 仍逐字為 `u1`～`u5-2-1`。第二次讀取前後總 row count 都為 90（69 legacy custom + 21 AF universal），沒有重複 seed。
- 最重要的 stable-slot gate 通過：AF manifest 維持 21 slots，DOM hash 仍為 `sha256:9778cb696f65745d53c5e2a8f4d6f443561b120fe4fe266fb429db31c9e4d43a`；21 個 current content rows digest 前後同為 `c460954e574d2ae1f9bf352a0cf189ae7fb24043f887c6c8564dd99b23294030`，11 個 current-ready rows digest 前後同為 `7386e60f30e9ba6da15a05fcaf7eabd37d4ce944adbf076c2488a92ce2bb064f`。
- scoped revision 下原 6 個有效 ready slots 完全保留：`u1-1`、`u2-1`、`u3-2-1`、`u4-2-2`、`u5`、`u5-2`。Contract test 同時鎖定 migration 前後 ordered slot IDs、manifest hash、6 ready rows、legacy custom IDs 與重跑 row count。
- 本施工包刻意未改 `ADA Guidelines` 或任何 heading，也未呼叫 LLM/RAG generation。驗收為 llmebm `31/31`、Python compile PASS、`git diff --check` PASS。下一個結構施工包必須先完成 per-slot structure revision，之後才允許 ADA 語意修正。

## Per-slot structure revision follow-up（2026-07-14 00:09 +08:00）

- 根因確認為 `status_for_topic()` 只比較整頁 `manifest_hash`，任一 heading/path 改動都會讓其他 20 個 current slots 一起 stale。修正後 `topic_content_versions.structure_revision` 保存每個內容版本所對應的 slot contract digest。
- digest 包含 `template_version`、`slot_id`、`heading`、`heading_path`、`level`、`content_target`、`allowed_blocks`；刻意排除 screenshot/viewport 與純 sibling order。移動到不同 parent 仍會因 `heading_path` 改變而 stale，但只調整同層顯示順序不重生臨床內容。
- status 現在優先逐 slot 比對 structure revision，再獨立比對既有 evidence revision。無法由 source manifest 回填的 legacy row 仍使用整頁 hash 保守 over-invalidate，不會把不確定的舊內容誤判 current。
- contract 先重現紅燈：只改第一個 slot heading 時，第二個 slot 被錯誤標 stale；修正後為第一個 `stale`、第二個 `ready`。另鎖定 layout-only order 不改 revision，以及 NULL legacy revision 重新啟動後可 idempotent backfill。
- live additive migration 後 AF 21/21 current rows 都有 structure revision、與各自 source manifest mismatch=0。21 current rows digest 仍為 `c460954e574d2ae1f9bf352a0cf189ae7fb24043f887c6c8564dd99b23294030`，11 current-ready digest 仍為 `7386e60f30e9ba6da15a05fcaf7eabd37d4ce944adbf076c2488a92ce2bb064f`。
- scoped evidence 下原 6 個有效 ready slots 仍為 `u1-1`、`u2-1`、`u3-2-1`、`u4-2-2`、`u5`、`u5-2`。重跑 migration 前後 51-row revision digest 同為 `43ecbb5d232f29014b7c5f37d7aaa48cc5c8b0e8d8edfc112774ed1873d9ad36`。
- `ADA Guidelines` 仍未修改，DOM hash 仍為 `sha256:9778cb696f65745d53c5e2a8f4d6f443561b120fe4fe266fb429db31c9e4d43a`；未呼叫 LLM/RAG generation。驗收為 llmebm `33/33`、Python compile PASS、`git diff --check` PASS、live health `ok`。

## Universal guideline heading correction（2026-07-14 00:27 +08:00）

- 共用 hierarchy 的 `u5-2-1` 從疾病專屬的 `ADA Guidelines` 修正為跨疾病成立的 `Professional Society Guidelines`。疾病專屬組織由該 topic 的 evidence/content 表達，不再硬編進 universal seed。
- SQL migration 只匹配 `source='universal'`、`node_key='u5-2-1'`、`name='ADA Guidelines'`；只更新 `name`，不改 `topic_uid`、`source`、`node_key`、parent、level 或 order。live backup 與 migration 後 21 個 AF slot_id 集合逐字相同，added=0、removed=0。
- contract 先重現舊 heading 不會遷移的紅燈，再鎖定 migration 後 target slot stale、父層 control slot ready；Sidebar contract `5/5`、完整 llmebm `34/34` 通過。
- local DOM+截圖 scanner 以 `--no-generate` 保存新 manifest，DOM hash 由 `sha256:9778cb696f65745d53c5e2a8f4d6f443561b120fe4fe266fb429db31c9e4d43a` 變為 `sha256:1c5641111e4e3baccaf4a5050c9056d3835dc1160538caafb678e8ca04b30195`；第二次為 `unchanged`，兩次 `generation=null`。
- 逐 slot manifest diff 只有 `topic-e55a50c1a5a4445c:universal:u5-2-1` 的 heading/heading_path 改變；structure revision mismatch 也只有該 slot。21 個 current content rows 指紋前後同為 `56cd8834d57e10e1887c34c880fd29d3f1b5bf35fb42e7489847a1f656240c3b`，證明未重寫內容。
- 新 screenshot artifact 為 `llmebm/data/runtime/topic_scans/5f5d409214e05ae1c377f5579dbff239cfa8d178582de009abe5f223af3f1f61.png`，layout 無破版、PHI 或第三方受保護內容。live `/api/health` 為 `ok`；曾誤查不存在的 `/health` 而得到預期 404，並非服務啟動失敗。

## DynaMed reference-layout candidate audit（2026-07-14）

- 先以同一套去文字 projection 比較目前 llmebm Topic Page 與一個已授權 DynaMed Topic Page；capture 只允許 landmark/ARIA 類型、bounded counts、generic state graph 與 screenshot SHA。任何 `text`、heading、title、URL、href、HTML、品牌、cookie 或幾何 box 都會被 strict schema 拒絕。
- Chrome 實測了 specialty group 展開、Topic tab 切換、section disclosure 展開/還原；永久 comparison 只保存 `tab-select`、`section-disclosure-toggle` 等 generic action，不保存控制名稱、醫學內文或來源 DOM。頁面最後已還原 Topic/default state。
- 保留 llmebm 自有優點：semantic `article`、左右 complementary regions、`aria-live/busy`、既有 slot/sidebar disclosure；共同能力為 Topic tabs 外觀、section state、ARIA control relations 與 skip navigation。
- 僅列為 `consider`、不得自動套用的能力：真正可切換的 Topic tabs、global search、fixed global header、back-to-top。實測 llmebm 的 Updates tab 點擊後仍為 `aria-selected=false`，因此不能把現有 tab 外觀宣稱為完成互動。
- `reference_layout_audit.py` 不連網、不讀 DB，也沒有 template/hierarchy mutation 路徑；輸出固定 `approval_required=true`、`production_mutation=false`、`slot_identity_policy=preserve_existing_ids`。comparison artifact 為 `doc/artifacts/llmebm-reference-layout-comparison-2026-07-14.json`，hash `sha256:0b39c3e43da1f611081e50560169944aea1c5e6b55d8b0030667a18df160b29c`。
- 原始 reference/current screenshots 與 sanitized capture JSON 只暫存在 `%TEMP%/llmebm-reference-layout-20260714/`，尚未複製進 repo。這次只抽樣一個 specialty flow 與一個 Topic Page，足以產生第一版 candidate，不足以宣稱覆蓋 DynaMed 所有 page families；擴大取樣前仍需人工確認本批能力取捨。

## llmebm-owned Topic navigation follow-up（2026-07-14 12:06 +08:00）

- 將原本只有外觀的 Topic/Updates/Images/Tables 四個按鈕收旂為兩個真實 view：`Topic` 與 `Updates`。兩者現有穩定 tab/panel ID、`aria-controls`、`aria-labelledby`、roving `tabindex` 與 `hidden` 關係；支援 click、左右鍵、Home 與 End。沒有對應內容的 Images/Tables 假 tabs 已移除。
- `Updates` 不生成醫學內文，只使用 sidebar 已計算的 per-slot `content_status` 與 content API 已存時間。特別鎖定 stale 以 sidebar 結構/evidence 比對為準，不得被底層 current row 的 `ready` 覆寫。
- 中間閱讀 pane 超過 320px 後才出現 llmebm 自有回頂按鈕，只操作 `#pane-middle.scrollTo()`，不依賴或複製第三方 footer/control。全域搜尋與 fixed header 仍保留在後續取捨，不進入本包。
- 可執行契約先為紅燈（缺 `bindTopicTabs`、缺 panel IDs），實作後實際 renderer/navigation self-check PASS；完整 llmebm `37/37`、Python compile、`git diff --check` 通過。live HTTP 已提供新 template/JS，`/api/health=ok`，RAG `/api/v1/rag/health=ready`。
- 資料不變性通過：21 universal slots、slot key digest `644333bd08054168d1c6e59289eda604e6f3944c053c48e66f7c4f820ecac535`、manifest hash `sha256:1c5641111e4e3baccaf4a5050c9056d3835dc1160538caafb678e8ca04b30195`、21 current content digest `56cd8834d57e10e1887c34c880fd29d3f1b5bf35fb42e7489847a1f656240c3b` 均不變。本包未掃描、未呼叫 LLM/RAG generation、未改 SQL 或 slot identity。
- Chrome 實際互動/視覺復驗尚有工具 blocker：Chrome control plugin 在 `browser-client.mjs:33` attach 時因 `Cannot redefine property: process` 中止。這不是 llmebm runtime 錯誤，但在外掛修復前不宣稱本次已完成新 UI 的 Chrome screenshot/visual PASS。

## DynaMed 參考結構匯入 Phase 三完成（2026-07-14 12:34 +08:00）

- 完成邊界是「參考一般 UI 能力後改良 llmebm」，不是匯入 DynaMed taxonomy/content。既有 sanitized comparison 經 strict hash/schema 復驗後，只核准 `interactive_topic_tabs` 與 `back_to_top`；`global_search`、`fixed_global_header` 明確 deferred。核准 plan 固定為 `hierarchy_changes=[]`、`external_data_retained=[]`、`runtime_dependency=false`。
- 新增 capability-only preview/apply CLI 與 SQL `reference_structure_imports` audit table。apply 只存 llmebm topic UID、plan/comparison/manifest hashes、核准能力與 slot-set digest；不保存來源 heading、醫學內容、URL、DOM、品牌或 screenshot。第一遍為 `applied`，第二遍為 `unchanged`，證明 transaction 冪等。
- stable-slot gate 通過：AF 仍為 21 slots，`slot_ids_sha256=sha256:998cb57447165a91b2b68dacbffee0a716acd7fa9eaf77731687e4583524ca09`；既有 universal node-key digest 仍為 `644333bd08054168d1c6e59289eda604e6f3944c053c48e66f7c4f820ecac535`。audit 路徑沒有新增 `source` 類型或 hierarchy node；manifest DOM hash 仍為 `sha256:1c5641111e4e3baccaf4a5050c9056d3835dc1160538caafb678e8ca04b30195`。
- 驗收中發現 scanner 原本只以 slot DOM hash 去重，visual-only UI 變更會刪除新 screenshot、沿用舊圖。root cause 已修正為 `visual_updated`：更新 screenshot evidence/manifest，但不觸發 content generation。第一次重掃為 `visual_updated`、第二次 `unchanged`，兩次 `generation=null`；新 screenshot hash 為 `f81d0a8a1716546fffb4700fdf54665843d329f08d57af4385fa1db714559d2c`，中央 pane 僅保留真實 Topic/Updates tabs，無舊 Images/Tables 假 tabs。
- llmebm `40/40` contracts、Python compile、JS syntax 與 renderer/navigation self-check 均通過；self-check 實際執行 click、ArrowLeft、Home/End、Updates stale precedence 與 back-to-top。live `/api/health=ok`。21 個 current content rows 的最新 `updated_at` 仍早於本施工包，證明本包沒有重寫臨床內容。
- Browser/Playwright CLI 額外互動 gate 有環境限制：主機沒有 `npx`，內建 Browser attach 仍因 `Cannot redefine property: process` 失敗。因此不宣稱本次有 Chrome click PASS；真實頁面 DOM/screenshot 由既有 Scrapling `DynamicSession` 成功取得，鍵盤行為由可執行 JS contract 驗證。
- `%TEMP%/llmebm-reference-layout-20260714` 的 4 個原始 artifacts（2 PNG、2 capture JSON，共 398234 bytes）已刪除。repo 只保留 sanitized comparison 與 [核准 receipt](artifacts/llmebm-reference-import-receipt-2026-07-14.json)。本次仍只抽樣一個 specialty flow 與一個 Topic Page，不宣稱覆蓋 DynaMed 全部 page families。

## Authorized reference heading correction（2026-07-15 11:56 +08:00）

- 以使用者已登入並明確授權操作的 Chrome 直接開啟 DynaMed `Atrial Fibrillation` Topic Menu，逐層展開至第三層；只比對 heading 文字、父子層級與順序並做視覺檢查，沒有保存文章內文、原始 DOM、URL、cookie 或來源 screenshot 到 repo。
- 目前 21 個 llmebm headings 中，13 個文字已逐字相同。只核准 3 個可證明語意範圍不變的一對一 rename：`u3-2 Testing → Testing Overview`、`u3-2-1 Laboratory Tests → Blood Tests`、`u4-1 Treatment Overview → Management Overview`。
- 5 個 llmebm 粗分類沒有一對一來源 heading：`Imaging`、`Medications`、`First-line therapies`、`Alternative therapies`、`Professional Society Guidelines`；硬套任一疾病細項會縮窄原 slot 意圖，所以本包不猜。`International Guidelines` 與 `United States Guidelines` 文字雖相同，但來源另有 `Guidelines` 中介層；為守住 stable-slot gate，本包不移 parent、不新增 slot。
- migration 只在 `source='universal'`、固定 `node_key` 且舊 heading 逐字相符時更新 `name`。AF 仍為 21 slots，`slot_ids_sha256=sha256:998cb57447165a91b2b68dacbffee0a716acd7fa9eaf77731687e4583524ca09`；parent、level、order、content target 全部不變，重跑冪等。
- own-page DOM＋screenshot scanner 第一次為 `saved`、第二次 `unchanged`，兩次 `generation=null`。manifest 由 `sha256:1c5641111e4e3baccaf4a5050c9056d3835dc1160538caafb678e8ca04b30195` 更新為 `sha256:461e70ba64ff0ccc09757a66b9c4c97a08a8d09b7a3d1801b0cc939d0bd15300`；新 screenshot hash 為 `c47748c154c7185687be4b587cca7d386ac2ef434d25e474bd978c96166333f5`，視覺檢查無破版。
- 前後 manifest 只有 `u3-2`、其兩個 descendants `u3-2-1/u3-2-2`、以及 `u4-1` 的 structure revision 改變。live 狀態由既有 5 stale 增加這 4 個為 9 stale；5 ready、7 insufficient_evidence 未被重寫。21 個 current content rows digest 前後同為 `6e500de9a654ade4682a59f77cffd069b7e23b026edd37642b33c94cf23954b1`，最新 `updated_at` 仍是 `2026-07-13T13:36:31.671280+00:00`。
- 可執行 gate 先紅燈確認舊 label 不會自動修正，再鎖定 slot identity、scoped stale、content row 不變與 migration rerun；完整 llmebm 為 `41/41`，Python compile 與 `git diff --check` 通過。Docker Desktop 起初未運行，啟動後 llmebm `/api/health=ok`；RAG 從 starting 恢復為 `ready`、140 indexed chunks，本包仍按設計沒有做 generation。

## Full DynaMed heading/hierarchy follow-up（2026-07-15 12:06 +08:00）

- 本節依使用者最新規則取代上一節的「無一對一則暫緩」決策：AF 現有 21 個 slots 只要與已授權擷取的 DynaMed Topic Menu 不同，就採 DynaMed 的 heading 與可由現有 slots 表達的父子層級，不再保留 llmebm 粗分類。
- migration 僅套用 `atrial-fibrillation`；不把 AF 特有的 `Rate Control`、`Cardioversion` 等標題污染其他疾病。非 AF topic contract 仍使用通用 template，測試鎖定 `type-1-diabetes` 保持 `Medications` 與原 guideline hierarchy。
- Diagnosis 現為同層 `Making the Diagnosis`、`Testing Overview`、`Blood Tests`、`Transthoracic Echocardiogram (TTE)`；Management 現為同層 `Management Overview`、`Treatment Setting`、`Rate Control`、`Cardioversion`；Guidelines 現為 `Guidelines and Resources → Guidelines → International Guidelines / United States Guidelines`。
- SQL 只更新 AF universal rows 的 `name`、`parent_id`、`layer_level`、`sort_order`；21 個 ordered slot IDs 逐字不變，`slot_ids_sha256` 仍為 `sha256:998cb57447165a91b2b68dacbffee0a716acd7fa9eaf77731687e4583524ca09`。Type 1 diabetes rows 前後不變，migration 重跑冪等。
- 相對上一版 manifest，共 8 個 slot structure revisions 改變：`u3-2-1`、`u3-2-2`、`u4-2`、`u4-2-1`、`u4-2-2`、`u5-1`、`u5-2`、`u5-2-1`。其中 4 個本來已 stale，本次新增 stale 為 `u4-2`、`u4-2-1`、`u4-2-2`、`u5-2`；live 總數成為 3 ready、5 insufficient_evidence、13 stale。
- 21 個 current content rows digest 仍為 `6e500de9a654ade4682a59f77cffd069b7e23b026edd37642b33c94cf23954b1`，最新 `updated_at` 仍是 `2026-07-13T13:36:31.671280+00:00`，證明沒有用新 heading 覆寫舊內文。
- own-page scanner 第一次為 `saved`、第二次 `unchanged`，兩次 `generation=null`；manifest 更新為 `sha256:0908f62b8fbcec5f58e9008b649389bec6dbe0a8b11cb56876af50f1844f1869`，screenshot hash 為 `fa0458972231a45f863f628c9250728b4fcd057b3db999f55ac101acef40d45f`，視覺檢查無破版。
- 完整 llmebm `41/41`、Python compile、`git diff --check` 與 live `/api/health=ok` 通過。現況是「既有 21 slots 全部使用來源文字/層級的精確子集」，不是「DynaMed AF 的所有目錄節點都已匯入」；來源中尚未被 21 slots 表達的 sections 必須在後續 additive-slot phase 另行新增，不能再拿既有 slot 改名硬塞。

## Acceptance cleanup（2026-07-15 12:50 +08:00）

- 使用者已明確同意上述 AF heading/hierarchy 交付；依原約定刪除 `_TEMP_LLMEBM_DYNAMIC_TOPIC_IMPLEMENTATION_DRAFT_2026-07-10.md`。本永久驗收報告、SQL、tests、DOM manifest 與 screenshot evidence 保留作為後續 additive-slot phase 的唯一交接基準。

## Blocker remediation follow-up（2026-07-13）

本節是同日後續實測，優先於下方原始 point-in-time 的 Phase E/剩餘 blocker 描述：

- 21-slot planner 現在先要求 compact full JSON（8192 output cap）；只有 provider `MAX_TOKENS`/明確 EOF 截斷才改走最多 6 slots 的 bounded batches，最後依 DOM 順序合併並重跑完整 manifest validator。截斷 JSON 不接受、不猜補。
- composer correction retry 現在會帶回前次 assistant JSON；兩次結構仍不合約時不保存任何模型臨床文字，而是產生 empty-block `insufficient_evidence`，並在 retrieval log 留下 `compose_fallback_reason=invalid_model_output`。provider/network 真錯誤仍維持 failed。
- llmebm 等待完整 Topic transaction 的 timeout 由 600 秒調整為 1800 秒；實測 21-slot sequential retrieval/compose 超過 600 秒，原設定會在 RAG 已完成 19/21 時丟棄整包結果。
- ingestion 新增 canonical `topic_key`、`slot_keys` 與 per-chunk `slot_keys_by_chunk`；SQLite `chunk_evidence_scopes` 及 `/api/v1/rag/topic-content/revisions` 產生逐 slot revision，llmebm 只比較該 slot。缺 metadata 時保守使用 topic/global wildcard。
- latest live job `12ef8b1f-4b50-4110-865c-0a93ecd5da64` 終態為 `completed`、errors=null。重啟後仍有 21/21 current rows：9 `ready`、12 `insufficient_evidence`、0 `empty/stale`、0 null revision。10 組 current citation 全部命中 indexed chunks。
- 精準 stale contract 以 temporary SQLite 實測：只更新 AF diagnosis scope chunk 時，AF diagnosis revision 改變，AF management 與其他 topic 不變；topic/global wildcard 仍按設計保守失效。
- 目前 legacy AF 27 個 indexed chunks 原本只有 disease metadata，migration 因而標成 `atrial-fibrillation/*`。新 code path 已支援 slot 精準，但這批歷史資料需以明確 `slot_keys_by_chunk` 重入庫；不可宣稱 legacy corpus 已完成 slot backfill。

因此原本的 planner 截斷 blocker 已關閉，21-slot transaction gate 已通過；醫學內容覆蓋只能宣稱 9/21 ready，其餘 12/21 是安全的 insufficient result，不可說整頁臨床內文已齊全。TEMP 草稿仍不得刪除，VM #10 仍未執行。

## 1～5 completion follow-up（2026-07-13 21:00 +08:00）

本節是目前最終狀態，優先於下方舊 point-in-time 數字：

1. **Legacy smoke — PASS**：將壞掉且回 402 的 `ebm_generate` binding 改綁已驗證的 Google chat connection 1；直接 task invoke、`/query`、`/check` 均為 `status=ok`。成功 query IDs 為 `d2cc783a-76bd-46ee-8ea8-6f97a4f4f4fe`、`8fff2454-4d59-4d6f-8285-90fbbb7b8e53`，兩條 legacy route 都保留 citation 與 contraindication hard gate。
2. **AF 精準 corpus — PASS**：`prepare_af_topic_corpus.py` 以人工審核 mapping 回填 24 個 `RM_AF_GUIDE_2023` + 3 個 `RM_AF_CONTRA_2023` legacy chunks，並由 bundled 公開 2023 ACC/AHA/ACCP/HRS guideline 的指定頁面走既有 Core1→Core2→Core3 索引 112 個 chunks。AF 合計 139/139 indexed、0 wildcard；全庫 140 indexed。第二次 `--verify-only` 為 `verified`，27 legacy unchanged，五個 slices 全部 exact。
3. **21-slot transaction — PASS（內容覆蓋仍 CONDITIONAL）**：job `5dcd126f-c615-422b-b62f-4a29cf36f435` 終態 `completed`、errors=null，沒有 planner truncation、provider error 或 composer fallback。DB current rows 為 11 ready + 10 insufficient；其中 5 個舊 ready 因新 evidence revision 而有效狀態為 stale，所以 UI/API 實際為 **6 ready、10 insufficient_evidence、5 stale**。這是 preserve-last-known-good safety，不可改寫成 21/21 ready。
4. **Affected-only stale/regeneration — PASS**：只 touch `RM_AF_GUIDE_2023:chunk:000001` 的既有 `universal:u5-2` scope，21 個 revisions 只有 `topic-e55a50c1a5a4445c:universal:u5-2` 改變；只送該 slot 的 job `e10b17cb-e390-490f-8ae3-08877c8b781d` completed 後 u5-2 回 ready，其他 slot 未重生。`/topic-content/revisions` 同時移除完整 readiness 依賴並將 scope 聚合改為 distinct chunk/grouped hashing，21-slot request 約 2.1 秒，不再誤落 global fallback。
5. **VM delivery gate — GATE PASS / LIVE BLOCKED**：`Use-Environment.ps1 -Profile vm` 現在只要 `vm.env` 還有 `# CONFIRM` 就在 backup/write 前 fail closed；實測 blocked=true 且 `.env` SHA-256 前後相同。尚缺 VM 內部 ebm-rag service DNS/port、llmebm/llmxx-server compose topology、TLS termination 與 topic-token secret ownership，故不能執行或宣稱 live VM E2E。repo 內兩個既有 public llmxx health 候選位址本次各以 15 秒 timeout 測試均不可達，且 public llmxx endpoint 本來就不足以推定內部位址。

### Final browser / citation / runtime evidence

- In-app browser 展開全部 DOM 後為 21 個唯一 `data-slot-id`；狀態 6 ready、10 insufficient、5 stale。點擊 `universal:u5-2` 後 article `aria-busy=false`、標題 `United States Guidelines`、13 筆 citation 可見，article 內 0 script、0 img、0 external link。
- Topic current content 共 88 次 citation occurrence、17 組唯一 `(paper_id, chunk_id)`；17/17 都存在於 RAG SQLite 且 `indexed_status=indexed`，missing=0。content 未發現 `data:image`、`<script`、`api_key` 或 bearer marker。
- Local scanner 再跑為 `unchanged`，DOM hash 仍為 `sha256:9778cb696f65745d53c5e2a8f4d6f443561b120fe4fe266fb429db31c9e4d43a`。
- Corpus 擴充後完整 `/health` 約 5.7 秒，原 healthcheck 內層 5 秒造成假 unhealthy；只把 healthcheck headroom 調為 12/15 秒，未移除或放寬 indexed-quality readiness。重建後容器為 healthy、failing streak 0。
- Final checks：llmebm `30/30`、ebm-rag `31/31`、Python compile PASS、JS syntax PASS、malicious renderer inert-text PASS、AF corpus verify-only PASS。

## Targeted retrieval correction follow-up（2026-07-13 21:36 +08:00）

- 盤點 unresolved slots 時重現 retrieval 共用根因：Epidemiology 與 Imaging 的 planner evidence needs 不同，但 `_build_queries()` 只取 `normalized_diagnosis=atrial-fibrillation`，因此兩者實際 deterministic queries 完全相同，舊 retrieval logs 也反覆取得相同 top hits。
- Topic orchestrator 現在把已驗證、bounded 的 `section.evidence_needs` 以 `case_context.retrieval_queries` 交給 Core4；Core4 先驗證並使用 explicit queries，再保留既有四條 generic disease queries。沒有該欄位的 legacy `/query`、`/check` 行為不變。
- Contract 先確認舊行為紅燈（`retrieval_queries` KeyError），修正後 targeted test PASS；完整 ebm-rag suite 更新為 `32/32`。Epidemiology 與 Imaging 的 query plans 已由 identical=true 變成 false。
- 僅對 Imaging `universal:u3-2-2` 執行一次 targeted job `f359251d-1536-4846-a7e1-08aa7fb6acc2`；job `completed`、error=null、約 51 秒。新 top hits 已包含 evaluation/basic clinical evaluation 與 echo 相關 chunks，證明 retrieval handoff 生效；但 composer 兩次仍回 schema-invalid，安全 fallback 保持 `insufficient_evidence`，沒有保存模型臨床文字。
- 15 個 unresolved slots 的最新結果分類：10 個 `composer_schema_invalid`、4 個 `evidence_gap_or_model_declined`、1 個不相干的靜態 `ADA Guidelines` heading。故下一優先是安全診斷 composer output shape，再處理真正 evidence gap；不可先大量抓文獻或放寬 validator。
- 本 follow-up 沒有新增外部文獻、沒有完整 21-slot generation、沒有連續 retry；有效狀態維持 6 ready、10 insufficient、5 stale。

## 本次實作修正

- 修正 manifest A→B→A 時舊 row 無法重新成為 current 的根因：每個 topic 只保留一個 `current`，其餘標成 `superseded`。
- 新增 A→B→A regression test；live scanner 從反覆 `saved` 恢復為第二次 `unchanged`。
- 新增 LLM query strategy 失敗時 deterministic fallback test。
- 新增零依賴 malicious renderer self-check，確認 `<img>/<script>` 攻擊字串只成為純文字。
- Topic/env 交付檔統一補上 `模組定位／主要責任／呼叫來源／輸入契約／輸出契約／安全邊界／維護提醒`，並由 test whitelist 鎖定。
- 移除具名第三方 editors 靜態文字與未使用的 mock CSS，改成 llmebm 自有 evidence 狀態說明。

## 原始 Phase 判定（修正前 point-in-time）

| Gate | 判定 | 實證 |
| --- | --- | --- |
| A — stable slots / safe renderer / UI states / keyboard | PASS | 29 個 llmebm contracts；malicious renderer PASS；Chrome 點擊後顯示內容；Tab 焦點為 2px solid outline。 |
| B — bounded DOM+screenshot scanner | PASS | local allowlist；DOM 與 expanded screenshot 同頁擷取；hash `sha256:9778cb696f65745d53c5e2a8f4d6f443561b120fe4fe266fb429db31c9e4d43a`；重掃 `unchanged`。 |
| C — vision planner / schema / capability / secret safety | PASS | Topic readiness、plan、compose 均 ready；invalid JSON、unknown slot/block、prompt injection、wrong capability contracts 通過；DB 無 image base64/key marker。 |
| D — retrieval / composer / citation / legacy compatibility | PARTIAL | query fallback、no-hit insufficient、slot isolation、source gate 通過；4/4 citations 可回指 indexed chunks。legacy `/query`、`/check`、`ebm_generate` route 仍存在，但本次為節省付費模型 token，未重跑 live behavior smoke。 |
| E — full generate / persistence / stale scope | PARTIAL | generate/version/restart/failure-preserve contracts與 browser persistence 通過；但 corpus-wide revision 仍使所有舊 revision slots 保守 stale，未達 affected-topic/slot 精準 invalidation。latest 21-slot full job 曾因 planner JSON 截斷而 `completed_with_errors`，目前為 1 stale + 20 empty。 |
| F — executable checks / live artifacts | PASS | py_compile、29 llmebm tests、19 ebm-rag tests、Node syntax/self-check、scanner、readiness、citation trace 與 Chrome screenshot 均完成。 |
| §14 — security / privacy / source integrity | PASS | scanner 不讀 cookie/localStorage/history；不提供任意 URL public scanner；截圖無 PHI；renderer 不執行模型 markup；DB 無 raw screenshot/key；未放寬 retrieval source gate；無 DynaMed 內容/asset。 |

## Live runtime evidence

- llmebm health：`http://127.0.0.1:33300/api/health` → `ok`。
- RAG Topic readiness：`ready=true`；evidence revision `sha256:bfbe2460ca813d87fae645398a0d0cd816fbf18f5f31874e8f67d2b168e549f8`。
- LAVA global、`topic_content_plan`、`topic_content_compose` readiness 均為 ready。
- Active Topic manifest：21 slots；DOM hash `sha256:9778cb696f65745d53c5e2a8f4d6f443561b120fe4fe266fb429db31c9e4d43a`。
- Expanded screenshot：`llmebm/data/runtime/topic_scans/5f5d409214e05ae1c377f5579dbff239cfa8d178582de009abe5f223af3f1f61.png`；視覺檢查無 PHI/第三方 editors。
- Chrome browser screenshot：[llmebm-dynamic-topic-p1-browser-2026-07-13.png](artifacts/llmebm-dynamic-topic-p1-browser-2026-07-13.png)。
- Browser DOM：Overview slot `data-content-status=stale`、article `aria-busy=false`、citation list 4 筆。

## Citation trace

目前 current content 的 4 個 citation 全部存在於 `ebm-rag/data/sys/database/rag_state.db` 且 `indexed_status=indexed`：

- `RM_AF_GUIDE_2023:chunk:000000`
- `RM_AF_GUIDE_2023:chunk:000004`
- `RM_AF_GUIDE_2023:chunk:000014`
- `RM_AF_GUIDE_2023:chunk:000015`

Topic DB 與 RAG DB 的 `data:image`、PNG base64 prefix、API-key marker 計數皆為 0；Topic manifest/content 的 DynaMed 字串列數為 0。

## 可重跑驗收

```powershell
llmebm\.venv\Scripts\python.exe -m py_compile llmebm\main_ebm.py llmebm\tools\scan_topic_manifest.py
llmebm\.venv\Scripts\python.exe -m unittest discover -s llmebm\tests -p "test_*.py"
C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --check llmebm\app\static\js\topic_content.js
C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe llmebm\tests\topic_content_renderer_check.js
docker cp ebm-rag\tests\test_topic_content_contract.py ebm_rag_web_ui:/tmp/test_topic_content_contract.py
docker exec -w /app -e PYTHONPATH=/app ebm_rag_web_ui python /tmp/test_topic_content_contract.py
llmebm\.venv\Scripts\python.exe llmebm\tools\scan_topic_manifest.py --url http://127.0.0.1:33300/topic/atrial-fibrillation --output-root llmebm/data/runtime/topic_scans --no-generate
git diff --check
```

Observed results（follow-up）：llmebm `30/30`、ebm-rag `29/29`、renderer malicious fixture PASS、scanner `unchanged`、`git diff --check` exit 0（僅 line-ending warning）。

## AF 121-slot 與 Topic UX 1～10 完成（2026-07-15）

- 已在使用者授權且登入的 Chrome 內將 DynaMed `Atrial Fibrillation` Topic Menu 全部 disclosure 展開，取得 121 個 heading nodes、12 個 roots、最深 4 層；永久實作只保留 heading、parent、level、order 與自有 stable key，不保存來源文章、URL、HTML、cookie、品牌 asset 或原始 screenshot。
- `Acceptance cleanup（2026-07-15 12:50）` 已依使用者同意刪除 TEMP 草稿；本段狀態優先於報告中更早的 point-in-time「TEMP 不得刪除」記錄。
- SQL migration 為純加法：21 個既有 `u*` slot keys 全數逐字保留，新增 100 個明確 `af-*` keys；live slot count 21→121、digest `c5f3e1113cb5c2a4ff375b37e0788045c5b8f310476671ad5e7907c3c7fa9954`。migration 前後 `topic_content_versions` 都為 50 rows、21 current，完整 digest 同為 `6e215c9007f721a4c2ace6f2fc8bbd73abf6bb57cf1236ced9fc94795ce198ae`，未重寫或遺失既有臨床內容。
- 完成並以 Chrome 實際操作 1～10：完整 taxonomy、Find in Topic、Previous/Next＋stable hash deep link、全 Topic Updates、References drawer、recommendation strength/certainty、Related Topics/sections、Patient Information、global heading search、Follow/Alerts＋Print/Cite/Share。所有動態文字仍只用 DOM text APIs；browser console error 為 0。
- Recommendation metadata 只允許 composer 從同一 cited evidence 複製短 grading phrase；沒有明確 grading 時 UI 顯示 `Strength: not stated`，不由 LLM 猜測。References drawer 去重 `(paper_id, chunk_id)`；Patient Information 新 slot 沒內容時保持明確 empty state，不把 clinician content 自動改寫成病人文宣。
- `topic_user_state` 以獨立 SQL table 保存 local follow、last seen time 與 status fingerprint；不寫入 RAG/vector DB。驗收先重現 Alerts 讀取後重整又回 13 的 bug，補 fingerprint 後當次 13→0、reload 仍為 0；最後已恢復 neutral `followed=false`。
- own-page DOM＋screenshot scanner 使用 scanner-only `.venv` 與 `--no-generate`：先 `saved`、UI state 改變後 `visual_updated`、最後 `unchanged`，全程 `generation=null`。current manifest 為 121 slots、DOM hash `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`、screenshot SHA `7589b23026676a062d14dadd9e288bbfc16c41a0db312c9a8e109d579590d674`。
- live 狀態為 3 ready、13 stale、5 insufficient_evidence、100 empty、0 failed。這是完整結構/UX 已完成但新增 slot 尚未生成的誠實狀態；本包沒有批量呼叫 LLM，也不宣稱 121-slot 臨床內文完整。
- 可執行 gate：llmebm `44/44`、ebm-rag container `33/33`、Python compile、Node syntax、malicious renderer self-check、`git diff --check` 全通過。llmebm `/api/health=ok`；RAG `/api/v1/rag/health=ready`、Qdrant healthy、LAVA plan/compose ready。原生 Print 點擊確實開啟 Chrome 列印流程；該 native dialog 會阻塞 DOM automation，驗收另開 clean tab 完成後續 DOM/screenshot，並未將此工具限制誤報成 app failure。

## AF bounded content generation follow-up（2026-07-15 14:30 +08:00）

- 新增 `llmebm/tools/generate_topic_content_batches.py`：預設只選 stale/failed、每批 1～6 slots、禁止 force 與 active-job 併發，依 manifest order 執行；每批落 `llmebm-topic-generation-run.v1` receipt。中斷後直接以 SQLite current status 續跑，不另建重複 queue。`--slot-ids` 只能縮小 eligible scope，不能把 ready 或其他未選狀態強塞進工作。
- Dry-run 精準選出 13 stale；live 依 5+5+3 規劃執行。第一個 5-slot batch completed；第二批因 TTE composer 收到 Gemini `finishReason=STOP` 但無 text parts 而 `completed_with_errors`，runner 按 gate 停止，第三批未送。完整 receipt：`llmebm/data/runtime/topic_generation_runs/atrial-fibrillation-20260715-141451.json`。
- provider root cause 修在共用 Google adapter：只對「空 text + STOP」多試 1 次；HTTP、schema、evidence 等其他錯誤不擴張重試。contract 先重現失敗再通過。RAG 重啟 ready 後只重跑 TTE 與先前未執行的 3 個 guideline slots；receipt `llmebm/data/runtime/topic_generation_runs/atrial-fibrillation-20260715-142634.json` 為 completed、error=null。
- 最終 live 狀態：**9 ready、5 stale、7 insufficient_evidence、100 empty、0 failed**。本輪新增 ready 為 `Epidemiology`、`Making the Diagnosis`、`Testing Overview`、`Blood Tests`、`Treatment Setting`、`United States Guidelines`；`Rate Control` 與 `TTE` 明確轉 insufficient。其餘 5 stale 都已嘗試，但新結果 evidence 不足，依 preserve-last-known-good 保留舊 current 內容，不再重燒。
- citation gate：9 ready slots 全部逐 block/item/row 檢查，19 組唯一 `(paper_id, chunk_id)`，缺 citation=0、空 ID=0。AF DB 為 64 total versions、21 current；舊 21 `u*` slot_id 集合逐字相同，manifest 仍 121 slots、DOM hash 仍 `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。
- 100 個新 `af-*` slots 本輪不生成：RAG SQLite 實測精準 `universal:af-*` scope rows=0；只有舊 `u*` scope rows=717 與 wildcard rows=5。現在生成雖可能取得 semantic hits，但後續相關 ingestion 不能保證只讓真正受影響的新 slot stale，違反 affected-slot gate。
- user flow 以本機 in-app browser 實測：`United States Guidelines` 正確顯示 evidence-only blocks 與 citation lists；`TTE` 顯示 `Insufficient retrieved evidence for this section.`，未生成假內容；stable hash、Previous/Next、Related Topics 正常，console errors=0，實際 screenshot 無溢出或破版。
- Final checks：llmebm `47/47`、ebm-rag container `34/34`、renderer self-check PASS、RAG/llmebm health ready/ok。第一次 host 呼叫因容器 token policy 回 403，沒有建立 job 或呼叫模型；runner 已補上送出前錯誤也保存 halt_reason 的 receipt 行為。

## 目前剩餘 blocker 與下一步

1. **最高優先**：對新增 100 個 `af-*` keys 建立 source/page/chunk → affected-slot 的人工審核 mapping；不可用 heading keyword 或 LLM 自動猜。先將現有 8 篇 AF corpus 能確定覆蓋的 slots 回填 scope，再逐批生成；其餘補正式來源後才生成。
2. 5 stale 與 7 insufficient 需要新增或重新切片的 evidence；evidence revision 未改前不重試。100 empty 也必須先通過上述 scope gate，不以 wildcard 內容灌滿頁面。
3. own-page scanner 的自動 generation 仍是舊的整包 request；完整產品化前應改接 bounded runner/queue 與 evidence-scope gate，避免未來 DOM change 一次送出大量 empty slots。
4. 取得實際 VM 內部 network/DNS/compose/TLS/secret topology，移除對應 `# CONFIRM` 後執行 live VM E2E。
5. 其他疾病目前仍使用 generic hierarchy；擴充下一個 disease 時必須重跑該疾病自己的 heading capture、stable-key additive migration、affected-slot mapping、DOM＋screenshot 與 user-flow gate，不得把 AF taxonomy 當全域模板。

## SQL 唯一 source of truth 施工包（2026-07-16 13:35 +08:00）

### 已實作

- `sidebar_nodes` 新增 `allowed_blocks_json`、`status`、`created_at`、`updated_at`；published rows 才進 runtime tree/search。`allowed_blocks_json` 已實際走完 SQL → sidebar API → DOM `data-allowed-blocks` → scanner/manifest，內容 renderer 的 block type 仍由程式安全 allowlist 限制。
- 新增 checksum-locked `schema_migrations` runner 與三個 immutable SQL artifacts：`001_condition_hierarchy_template.sql`、`002_atrial_fibrillation_hierarchy.sql`、`003_reviewed_heading_corrections.sql`。generic 21-slot template 與 AF 121-slot topic seed 都由 SQL 保存。
- production Python 已移除 `UNIVERSAL_TEMPLATE`、`ATRIAL_FIBRILLATION_REFERENCE_TAXONOMY`、`REFERENCE_HEADING_MIGRATIONS` 與 `_ensure_universal_nodes`。新 topic 只在完全沒有 universal rows 時由 SQL seed materialize；既有 topic rows 不會在 read/restart 時被 Python taxonomy 回填或校正。
- 加入 checksum tamper fail-closed、legacy DB/6 ready preserve、AF 21→121 additive migration、post-migration heading restart-preserve、SQL allowed-block propagation，以及 fresh SQL seed → 121-slot exact manifest hash gates。

### 多重驗收實證

- 修改前基線：llmebm `47/47`；AF 121 slots；slot-set SHA `c5f3e1113cb5c2a4ff375b37e0788045c5b8f310476671ad5e7907c3c7fa9954`；舊十欄 row digest `7bc6c69fdcee62a35aa4887f2f2ba5092c8c82c6584845d82367c649ba3484f4`。
- production DB 複本連跑 migration 兩次：121 slots、上述兩個 digests、manifest `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6` 均不變；3 receipts、121 AF seed rows、14 sidebar columns 正確，第二次 all-row digest 完全相同。
- live migration 前後四張內容表完全相同：`topic_manifests` 10 rows／`0d6f...e61b`、`topic_content_versions` 65／`8cd3...bb1c`、`topic_generation_jobs` 21／`d760...d61`、`topic_user_state` 2／`045a...9ff0`。沒有重寫或遺失臨床內容。
- live container 再 restart 兩次：sidebar all-row digest 均為 `sha256:e609e373ea6329273a037f3be40a72514a3d724be2249b4d793b0391b485803f`；llmebm health `ok`，RAG `ready`、8 papers／140 chunks、LAVA plan/compose ready。
- 最終 regression：llmebm `51/51`；ebm-rag container `34/34`＋live fallback `2/2`；Python compile、JS syntax、renderer self-check、`git diff --check` 均 PASS。
- bounded scanner `unchanged`、generation=null；artifact 仍為 121 slots、12 roots、max level 4，screenshot SHA `7589b23026676a062d14dadd9e288bbfc16c41a0db312c9a8e109d579590d674`。人工視覺檢查無 overflow、遮擋、層級錯亂、PHI 或第三方資產。
- headless user-flow 實際展開 `u1` 並點 `Evaluation`：`data-content-status=ready`、active slot 正確、內容 3913 characters、console errors=0、page errors=0。DOM 同時驗得 121/121 unique slot IDs、23 disclosures ARIA 完整、所有 slot allowed-block JSON 合法。

### 過程狀況與未完成

- Docker Desktop 原先未執行；啟動後 `restart: unless-stopped` 自動恢復 llmebm，正式 migration 比原定人工備份步驟先執行。由於 migration 前 digests 與 production-copy rollback source 已先保存，且 live 前後所有 identity/content digests 完全相同，未觸發回復；未來正式 deployment runbook 仍須補明確 stop→backup→migrate→verify→rollback 流程。
- host `ebm-rag/venv` 的 interpreter path 已失效；本次改用健康的正式 container dependencies 跑完 36 tests。重建 host venv 是開發環境維護項，不影響 live RAG。
- scanner 在 `unchanged` 時 CLI 會回報預定 output path，即使該次不重寫 JSON；有效 current artifact 仍在 canonical scan root。這是低風險 observability 瑕疵，尚未在本 SQL 施工包擴張修改。
- 產品總完成度依 Master Checklist 為 **48.7%**。下一個硬 gate 是對 100 個 `af-*` slots 建立人工審核 source/page/chunk → affected-slot mapping；完成前仍禁止批量生成 100 empty。

## P0～P7 continuation package（2026-07-16 16:05 +08:00）

### 本輪已實作

- AF affected-slot mapping 已擴充為 100 個 `af-*` slots：54 個 generation-eligible、46 個依 gap/partial/metadata/proprietary 狀態 fail closed；121/121 slots 都有逐 slot evidence revision，未知 mapping 不會回退成 corpus-wide 猜測。
- AF corpus 現為 9 個 paper IDs、177 AF chunks。9/9 source policies 皆明確保存 lifecycle、license 與用途決策；目前商用發布全為 `permission_required`／not approved，所以醫療 publish gate 會拒絕，沒有用「公開可讀」冒充「可商用授權」。
- medical review/publish backend 已涵蓋 generated、review_pending、approved、published、rejected、retired、audit、preserve-last-published 與 rollback；Topic/Hierarchy Admin backend 已涵蓋 rename/reorder/move/retire/restore preview/apply/rollback、完整性檢查及 audit。
- mutation endpoints 已分離 review/hierarchy token、constant-time 比對、CORS allowlist、bounded generation schema 與本機 30 requests/client/path/60s ceiling；正式 identity/RBAC、distributed rate-limit 仍未冒充完成。
- References drawer 新增受控 source-detail 路徑：RAG 只輸出 public allowlist，llmebm 只允許查目前 slot 真正引用的 paper IDs。Browser DOM 與 screenshot 已確認 organization、publication year、DOI、document version、source status、license status 及 commercial-publication decision。
- Topic responsive layout 修正 390px main width=0/水平溢位根因；mobile navigation/resources 改為可關閉 overlay，長 citation 會換行，H1 顯示人類可讀疾病名而非內部 UID。
- 新增純標準函式庫 SQLite backup/restore 工具、integrity check、SHA-256 gate、atomic replace、pre-restore safety backup 與維運 runbook；live `topic_content.db` 臨時 backup/restore drill digest 一致。

### 多重驗收結果

- llmebm unittest：`61/61` PASS。
- RAG Topic contract：`39/39` PASS；source policy：`6/6` PASS；affected-slot map：`2/2` PASS；synthetic fallback：`2/2` PASS。
- renderer inert-text/navigation self-check PASS；`git diff --check` exit 0（只有既有 CRLF 提醒）。
- `prepare_af_topic_corpus.py --verify-only`：`status=verified`、mapping hash `sha256:29433b2cc8c32376c97fd608735f6113658007368ba949da9a93e361ff7e2769`、177 AF chunks、9 policies、0 updates、0 pending updates。
- live health：llmebm `ok`；RAG `ready`；Qdrant healthy。AF status 仍為 **1 ready、25 stale、95 empty、0 failed**，沒有把 stale draft 說成 current ready。
- Browser artifacts：`runtime_reports/llmebm-browser-qa-2026-07-16/af-definitions-desktop-fixed.png`、`af-definitions-mobile-390-fixed.png`、`af-mobile-navigation-overlay.png`、`af-mobile-resources-overlay.png`、`af-source-details.png`。來源詳情同時由 DOM semantic snapshot 與實際畫面驗收。
- 本輪新增／修改的 Python、JavaScript、HTML、tests 與 runbook 均保留模組定位、主要責任、呼叫來源、輸入／輸出契約、安全邊界、維護提醒 header；非顯然邏輯旁保留維護 note。

### 尚未完成與外部 blocker

1. 目前 9 個 AF 來源都沒有商用發布許可；授權／法律確認前不可把 54 個 eligible slots 批量發布，也不能宣稱 AF 整頁內容完成。
2. 95 empty、25 stale 尚需合法來源、targeted generation、medical review 與 publish；full-page gate 仍未通過。
3. reviewer identity/RBAC、claim/content/citation diff UI、Hierarchy Admin UI、source withdrawal/conflict/diversity/supersession 仍缺。
4. Images/Tables、study details、feedback queue、完整 Updates change log、正式全域 IA、真實導覽 routes、accessibility/device matrix、localization 仍缺。
5. durable queue/worker、idempotency、metrics/cost、scheduled encrypted retention、VM DR、multi-disease golden set 與 VM E2E 仍缺。
6. 同機 `rootmedicals_paq_v8` 於本次驗收時處於 restarting；它不是 llmebm/RAG/Qdrant 路徑，本輪未越權改動，但正式整套 RootMedicals 驗收前必須另行診斷。

依 Master Checklist，本輪總完成度為 **62.4%**；來源詳情已由 partial 升為 complete，但上述 gate 尚未完成，因此仍不可宣稱 llmebm 已達完整或能賣等級。

## Medical Review Admin 1～10 施工包（2026-07-16 16:44 +08:00）

### 本輪 1～10 已實作

1. Review token 使用 password input，僅以 `X-LLMEBM-Review-Token` header 傳送；不進 URL、log、localStorage 或 sessionStorage。
2. 可輸入／記住非敏感 topic slug，載入 canonical topic 的 current-ready review queue。
3. Queue 顯示 heading、workflow、content version 與來源數；只列 current revision 的 `status=ready`，不把 stale last-known-good 混入。
4. 可依 generated、review_pending、approved、rejected、published、retired 過濾。
5. Slot list 使用 native button 與選取狀態，保留 keyboard/focus semantics。
6. Current draft 以 safe DOM 元件顯示 clinical blocks 與 citations，不用 `innerHTML`。
7. Published comparison 顯示目前 published version；沒有 published 內容時明確 empty state，不虛構差異。
8. Source gate 顯示逐來源 license/use decision；commercial publication 未允許時 publish/rollback fail closed。
9. Audit history 顯示 actor、時間、before/after 與 comment；無紀錄時明確 empty state。
10. Submit、Approve、Reject、Publish、Rollback action 已接既有 lifecycle API；每次 action 強制 comment，狀態不合法或 source gate 未過時按鈕停用，完成後重載 queue/detail。

### 多重驗收與實際修正

- Red→green contracts：新增 queue/filter 與完整 Admin surface tests；host llmebm suite `63/63` PASS，正式 container image smoke `19/19` PASS。
- Python compile、兩支 JavaScript syntax、renderer inert-text/navigation self-check與 `git diff --check` 均 PASS；diff check 只有既有 CRLF 提醒。
- 隔離 live API：未帶 token 為 403；帶測試 token後 queue/detail 成功。第一次發現 raw DB `ready` 16 筆包含 stale，修成 evidence/structure revision live-status 過濾後只回 1 筆 current-ready。
- Chrome desktop DOM＋screenshot：實際載入 1 筆 `United States Guidelines`、current draft、3 個來源、source gate blocked、audit empty；空 comment submit 顯示安全 validation error，沒有修改共享 live DB。
- Chrome accessibility 修正：slot queue 從錯誤的 `button role=listitem` 改回 native button semantics。
- Chrome 390px 修正：第一次 `document/body=410px` 有水平溢位；補 mobile top-bar wrap 與 static asset cache-buster 後，最終 `innerWidth=390`、document/body `375px`、console 0 error/warning。
- 最終 artifacts：`runtime_reports/llmebm-review-admin-qa-2026-07-16/medical-review-desktop.png`、`medical-review-mobile-390-final.png`。pre-fix screenshots 只作 bug evidence，不列為 PASS。

### 未解除 gate

- 正式 `rootmedicals_llmebm_ui`（33300）目前沒有 `LLMEBM_REVIEW_ADMIN_TOKEN`／相關 admin token；正式 API 正確 fail closed。隔離 QA container 使用 test-only token 通過，不等於 production 已啟用。
- Browser 為避免污染共享 DB，只實測 queue/detail/filter、action gating 與 validation error；完整狀態 mutation 由 transaction/unit contracts 覆蓋，仍需正式 reviewer identity、合法測試 topic 與 reviewer UAT 後才能作 production click-through。
- claim/study/citation semantic diff、正式 reviewer identity/RBAC、tablet/screen-reader/WCAG matrix、授權來源與完整 AF medical review/publish 仍未完成。
- 本輪沒有改變 Master Checklist marker 數量，產品總完成度維持 **62.4%**，不得宣稱完整或能賣等級。
