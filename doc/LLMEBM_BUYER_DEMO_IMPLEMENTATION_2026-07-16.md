<!--
模組定位: llmebm Buyer Demo Phase 1～10 的累積施工與驗收永久紀錄。
主要責任: 保存每一 Phase 的 observable outcome、獨立 gate、累積 gate、artifact 與未完成事項。
呼叫來源: 本次大工程、後續 handoff、Master Checklist 更新與最終 buyer UAT。
輸入契約: 使用者核准的十階段順序、repo live state、可執行測試、DOM 與 screenshot artifacts。
輸出契約: 不跨 phase 冒充完成；每階段只有在獨立及累積驗收都通過後才標 PASS。
安全邊界: 不保存 token、cookie、PHI、第三方文章全文或未驗證醫療結論。
維護提醒: 驗收失敗必須留在當前 Phase 診斷；下一位 agent 以最末 PASS 段落為續工起點。
-->

# llmebm Buyer Demo Implementation Record

## Phase 1 — Reviewed eight-slot scope freeze

狀態：**PASS**（2026-07-16 20:38 +08:00）

### Observable outcome

- 版本化 artifact 精確保存 8 個 current SQL slot IDs、headings、approved source IDs、demo-level decision、reason、reviewer label 與 clinical re-confirmation gate。
- 明確排除舊 snapshot 的 `u4-2`、`u5-2`，US guideline 使用 current `u5-2-1`。
- wildcard、`AR`、`RM_76EA6AB0` 不得進 artifact；review hash 可重現。
- 本 Phase 不動 DB、不生成、不做 workflow transition。

### Independent gate

- Red：缺 `load_demo_scope_review` 與版本化 JSON，新增兩項 tests 先以 AttributeError/FileNotFoundError 失敗。
- Green：AF affected/demo mapping `4/4 PASS`、Python compile、targeted diff check PASS。

### Phase 1 cumulative gate

- AF mapping `4/4 PASS`。
- RAG source policy `6/6`、Topic contract `39/39`、synthetic fallback `2/2` PASS。
- llmebm `63/63 PASS`；RAG health `ready`。
- review hash：`sha256:7afebe03dd7e07c43fce8589db28649e891e0b4e93a541bfceefa62197ae540e`，連續載入一致。
- runtime DB forbidden scope rows：0。

### Observed—not hidden

- host `ebm-rag/venv` 綁定已不存在的舊 Python；Phase 1 改以 bundled Python 跑純標準函式庫 cross-repo tests，正式 container dependencies 跑其餘 47 個 RAG tests。
- container 原無 tests，第一次命令的 test failure 曾被後續 health exit code 蓋掉；已拆成獨立命令並依各 test file 的 repo-root contract 分流，最終真實全綠。
- live scope 仍有三個待 Phase 3 修正：`u2-1` 多 GUIDE、`u4` 多 GUIDE/RISK、`u5-2-1` 為 0 rows。這些不屬 Phase 1 artifact freeze，因此未假裝已修。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-01-scope-freeze.json`

## Phase 2 — Append-only review provenance

狀態：**PASS**（2026-07-16 20:47 +08:00）

### Observable outcome

- RAG SQLite 新增 `evidence_scope_reviews`；每筆保存 batch、topic/slot、mapping version、decision/reason、reviewer/date、review hash、approved sources、exact chunk/source-version snapshot 與 scope revision。
- UPDATE/DELETE triggers 強制 append-only；相同 artifact＋相同 scope 重跑 `inserted=0`。
- current gate 同時要求 decision=approved_demo、非空 exact scope、source set 完全相同、scope revision 完全相同。
- CLI 預設唯讀；只有 `--apply` 建 schema／append rows，輸出 reviewed 與 current_approved 分離的 JSON receipt。

### Independent gate

- Red：Topic contract 新增兩項後因 `record_evidence_scope_review_batch`／`get_evidence_scope_review_statuses` 不存在而失敗。
- Green：Topic contract `41/41 PASS`；append-only、idempotence、re-ingestion preserve、source/scope stale 與 wildcard/empty rejection 全部執行。
- live apply 前 SQLite backup integrity=ok；第二次 apply inserted=0。
- migration 前後 papers 11、chunks 182、chunk scopes 1345 的 count＋SHA-256 完全相同。

### Live result

- 8/8 reviewer decisions 已記錄；5/8 current-approved。
- 待 Phase 3 對齊：`u2-1=source_set_mismatch`、`u4=source_set_mismatch`、`u5-2-1=empty_scope`。
- 這三筆沒有被手動升級，也沒有因 reviewer decision 已存在就冒充 current。

### Phase 1+2 cumulative gate

- Phase 1 mapping `4/4`、RAG source policy `6/6`、Topic contract `41/41`、synthetic fallback `2/2`、llmebm `63/63` 全通。
- Python compile、diff check、RAG health ready。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-02-review-provenance.json`

## Phase 3 — Precise scope alignment and review visibility

狀態：**PASS**（2026-07-16 21:08 +08:00）

### Observable outcome

- 8 個 buyer-demo slot 的 current source set 全部與 reviewer artifact 完全一致；`reviewed=8`、`current_approved=8`、`pending=0`。
- removal-only gate 只會從 8 個 reviewed slot 移除未核准來源，不會自動替任何 slot 增加臨床 scope。
- US guideline 從舊 `u5-2`（International Guidelines）精準移至 current `u5-2-1`（United States Guidelines）。
- RAG 新增 bounded read-only scope-review status API；llmebm Medical Review Admin 可在沒有 generated content 時獨立顯示 mapping review。

### Independent gate

- Red：新增 allowlist/current-guideline tests 後，舊程式缺 enforcement helper 且仍指向 `u5-2`；新增 Admin contract 後，舊 UI 在 current-ready queue=0 時無法展示 mapping review。
- Green：AF mapping `6/6`、RAG Topic contract `42/42`、llmebm Topic contract `53/53`，Python compile、JS syntax、renderer check 全通。
- live `prepare --verify-only` 由預期 mismatch 轉為 `verified`；同一 review artifact append-only 新增 3 筆 observed scope rows，總計 11 rows／8 distinct slots。

### Exact affected-slot proof

- 121 個 AF slot 中只有 4 個 scope membership 改變：`u2-1`、`u4`、`u5-2`、`u5-2-1`。
- `u2-1` 移除 GUIDE 4 chunks；`u4` 移除 GUIDE/RISK 23 chunks；`u5-2` 的 15 chunks 移至 `u5-2-1`。
- papers 11 筆與 chunks 182 筆的內容／vector identity hash 前後完全一致；forbidden core scope rows=0；DB integrity=ok。

### DOM + screenshot gate

- 隔離 QA container `33302` 使用 test-only review token；沒有做 review/publish mutation。
- 真實 Chrome DOM：`8/8 reviewed mappings current`、`121 topic slots checked`、8 筆 reviewed item、console errors=0。
- 視覺證據：`doc/artifacts/llmebm-buyer-demo-phase03-scope-review-2026-07-16.png`。

### Phase 1+2+3 cumulative gate

- AF mapping `6/6`、RAG source policy `6/6`、Topic contract `42/42`、synthetic fallback `2/2`、llmebm `63/63` 全通。
- renderer、Python compile、JS syntax、diff check、RAG API、Docker health、SQLite integrity 全通。
- Phase 1／2 receipts JSON 仍有效且 SHA-256 已鎖入 Phase 3 receipt。

### Observed—not hidden

- `/health` 完整 readiness 約需 6 秒；2 秒 timeout 曾誤判，30 秒 gate 回 `ready`、Docker 回 `healthy`。後續 smoke 不得再用 2 秒作結論。
- production `33300` 尚未配置 review-admin token；Phase 3 UI 已在隔離 QA surface 驗證，但 production token configuration 留在後續 demo operations package。
- current-ready queue 在 Phase 4 生成前為 0，這是目前真實狀態，不宣稱已有 demo 內容。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-03-precise-scope-and-review-visibility.json`

## Phase 4 — Bounded reviewed-slot generation

狀態：**PASS**（2026-07-17 18:15 +08:00）

### Observable outcome

- 只生成 reviewer artifact 核准的 8 個 slot；server submission gate 對任何非 current-approved slot fail closed。
- 8/8 current versions 為 `ready`，共 16 blocks、44 citations；全部由 `google/gemini-2.5-flash` 生成，workflow 仍明確標為 `generated`。
- 44 個 citation 逐筆反查 `paper_id + chunk_id + atrial-fibrillation + exact slot scope`，0 筆越界或遺失。
- manifest 維持 121 slots，hash 固定為 `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。

### Independent gate

- Composer red→green contracts：provider metadata envelope、truncated JSON clean retry、candidate-evidence bounded reconsideration、exact-citation minimum template、top-3×2000-character prompt bound。
- Planner red→green contract：每個 allowlisted retrieval filter 都在 prompt 內宣告精確 value type；validator 未放寬。
- RAG Topic contract `47/47`；8/8 scope review current-approved；RAG `ready`、llmebm `ok`。

### DOM + screenshot gate

- 真實 Chrome 逐一點擊 8 個 sidebar slot：8 heading matches、8 `data-content-status=ready`、8 有可見 citation、0 placeholder、console errors=0。
- 視覺證據：`doc/artifacts/llmebm-buyer-demo-phase04-8-ready-2026-07-17.png`（SHA-256 `2a1ba215fcba3ce0a76899771063540dabd7ece36c4498d719f6effc0950c0dd`）。

### Phase 1+2+3+4 cumulative gate

- AF scope mapping `6/6`；RAG 其餘 contract `55/55`；llmebm `64/64`；總計全部通過。
- 4 個 browser JS syntax、renderer inert/navigation contract、Python compile、diff check、secret/placeholder scan 全通。
- Phase 1–3 receipts 的 SHA-256 已鎖入 Phase 4 receipt。

### Observed—not hidden

- 第一版長 prompt 造成 schema envelope、截斷與過度保守 fallback；最後以 top 3 evidence、每段 2000 字的 bounded prompt 收斂，降低 token 與雜訊，citation gate 仍對完整 bundle 驗證。
- 一次 canary 因 planner 產生數值型 `min_ocebm_level` 而 `completed_with_errors`；補 filter type contract 後相同 canary 不再出現 plan error。
- 8 個內容目前都仍是 internal generated draft，Phase 4 不等同醫療審閱／發布完成。
- `Management` 現有摘要聚焦 PALLAS/dronedarone，對整體管理代表性不足；列為 Phase 5 reviewer 必修，不以 citation 正確掩蓋語意品質問題。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-04-bounded-generation.json`

## Phase 5 — Review readiness and draft preview

狀態：**PASS**（2026-07-17 18:42 +08:00）

### Observable outcome

- `Management` 由單一 PALLAS/dronedarone 摘要修正為 3 blocks／3 citations，分別涵蓋 rhythm control、rate-control safety、anticoagulation，引用 3 個 reviewed sources。
- corrected 8 slots 全部進入 `review_pending`；audit 共 8 筆 `submit`，actor 固定為 `buyer-demo-preflight`，note 明寫仍需 clinical reviewer approval。
- 沒有偽造醫師簽核：`approved=0`、`published=0`；commercial source gate 仍 blocked，Admin 的 Publish 按鈕維持 disabled。
- 8 slots 合計 18 blocks、46 citations、7 unique papers；逐筆反查 exact paper/chunk/slot scope，invalid=0。

### Independent gate

- 新增 3 個 red→green contracts：source-diversified bounded prompt、broad-section under-coverage retry、Gemini opt-in JSON response mode。
- 第一個 `u4` job 確實出現 truncated JSON，但舊 ready version 被保留；修正後只重跑 `u4`，第二個 job 產生 version 100，另外 7 slots 未重生。
- RAG 全測 `63/63`、llmebm 全測 `64/64`、renderer inert/navigation、Python compile、JS syntax、compose config、diff check 全通。
- phase 前 SQLite backup integrity=ok；live DB phase 後 integrity=ok；RAG readiness=true、llmebm health=ok。

### DOM + screenshot gate

- Topic DOM：121 個 slot buttons；點擊 Management 後 `data-content-status=ready`，顯示 3 個 cited sections，且有 `Internal generated draft — not medically reviewed or published.`。
- Admin DOM：8 queue items、`8/8 reviewed mappings current`、Management badge=`review_pending`、Approve/Reject 僅供真實 reviewer、Publish disabled、audit note 可見。
- 視覺證據：
  - `doc/artifacts/llmebm-buyer-demo-phase05-management-preview-2026-07-17.jpg`
  - `doc/artifacts/llmebm-buyer-demo-phase05-review-queue-2026-07-17.jpg`

### Phase 1+2+3+4+5 cumulative gate

- manifest 仍為 121 slots，hash 仍為 `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。
- Phase 1–4 receipts SHA-256 已鎖入 Phase 5 receipt；既有 scope/migration/stale/generation/review lifecycle tests 全數回歸通過。
- Review token 只以 runtime secret 注入；compose 未配置時仍 fail closed，browser token 欄位不寫 browser storage。

### Observed—not hidden

- Gemini 一般文字模式即使要求 JSON，仍曾在 484 characters 處截斷；只對 topic composer 啟用 `application/json`，不改其他 LAVA chat caller 的文字契約。
- Phase 5 的 PASS 是「工程 review-readiness + demo draft preview」；不是 clinical approval，也不是 publish-ready。
- 真實臨床 reviewer 的 approve/reject 決策仍是外部人工 gate；本階段刻意停在 `review_pending`。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-05-review-readiness.json`

## Phase 6 — Buyer-readable AF Topic page

狀態：**PASS**（2026-07-17 19:03 +08:00）

### Observable outcome

- Topic header 直接顯示 `Demo preview`、version、stored update time 與 `Clinical review pending`；買家不必進 Admin 才知道內容狀態。
- Overview、Description、Diagnosis、Making the Diagnosis、Management、United States Guidelines、Thromboembolic Prophylaxis、Embolic Stroke/Thromboembolism 共 8 個 demo slots 仍為 `ready / review_pending / draft_preview`。
- Updates 依 stored time 排序，顯示 status、workflow、version、timestamp；當前統計為 8 ready、20 stale、93 empty，沒有把整頁誤稱為完成。
- Management References 去重後為 3 筆，source details 亦為 3 筆，組織、年份、DOI、版本、來源狀態與 publication gate 均可見。
- 沒有資料時不顯示 Images 假分頁；既有安全 component/table renderer 原樣重用。

### Independent gate

- 3 個 Phase 6 red→green contracts：bounded status metadata、真實 trust header/Updates contract、renderer trust-state self-check。
- llmebm `65/65`、RAG `63/63`；renderer inert/navigation、Python compile、JS syntax、compose config、diff check 全通。
- touched production/test files 的 7 欄 header comment contract `5/5` 通過。

### DOM + screenshot gate

- 6 個買家必看 sections 以獨立 deep link 逐一載入：6/6 active slot 正確、內容非空、ready、review_pending、draft_preview、version 可見、draft warning 可見。
- Management DOM：version 100、3 reference items、3 source-detail items；Updates DOM：28 stored non-empty entries，含 `Latest stored section changes` 與 version/workflow/time。
- browser console errors=0；桌面截圖人工檢視可讀、沒有重疊或假 Images tab。
- 視覺證據：
  - `doc/artifacts/llmebm-buyer-demo-phase06-trust-header-2026-07-17.jpg`
  - `doc/artifacts/llmebm-buyer-demo-phase06-updates-2026-07-17.jpg`

### Phase 1+2+3+4+5+6 cumulative gate

- manifest 維持 121 slots，hash 維持 `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。
- Phase 1–5 receipts SHA-256 已鎖入 Phase 6 receipt；既有 migration、precise stale、scope review、generation、review lifecycle 全部回歸通過。
- RAG container healthy、llmebm health=ok；8 個 demo slot 的版本與 workflow 狀態在 public bounded status contract 與單 slot content contract 一致。

### Observed—not hidden

- Phase 6 是 bounded AF buyer demo page，不是 121-slot full generation：非 demo 範圍仍有 20 stale、93 empty。
- 臨床簽核仍未發生；8 個 slot 全部清楚標成 demo preview / clinical review pending。
- tablet/mobile/keyboard/broader buyer journey 屬 Phase 8，沒有用桌面截圖提前宣稱完成。
- `docker compose config` 通過，但仍提示既有 top-level `version` 已 obsolete；本階段不擴張 scope 處理非阻塞清理。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-06-buyer-topic-page.json`

## Phase 7 — Product entry, routes, search and dead links

狀態：**PASS**（2026-07-17 19:15 +08:00）

### Observable outcome

- 首頁新增 Featured buyer demo 真實入口；不帶 fragment 開啟 AF 時自動落到 Overview and Recommendations，不再停在空白等待選擇。
- 首頁 search 改接 `/api/v1/search`：輸入 `stroke` 回傳唯一的 `Embolic Stroke and Thromboembolism — atrial fibrillation`，點擊後落到正確 `af-prognosis-stroke` slot。
- 舊的 `${query} fibrillation` 假 suggestion 已移除；search result 全部以 text-only DOM 建立。
- index/topic 公開頁 `a[href="#"]` 都為 0；Specialties 走 `/`，尚未完成的產品模組明確 `aria-disabled`，不再假裝可點。
- topic → Specialties → home 返回路徑已以 Chrome 實測。

### Independent gate

- 3 個 Phase 7 red→green contracts：公開頁零 hash-only link、owned API search/deep link、Featured AF entry/default overview。
- route gate：`/`、AF topic、Admin、health、specialties、search 全部 200；`/topic/Atrial%20Fibrillation` 308 到 canonical slug。
- llmebm `68/68`、RAG `63/63`；renderer、2 個 JS syntax、compose config、diff check 全通。
- touched code/template header contract `5/5`；browser console errors=0。

### DOM + screenshot gate

- 首頁：48 specialties、4 個 disabled future-nav items、0 個 hash-only links、Featured link 指向真實 AF route。
- 搜尋：dropdown 顯示 1 個真實 stroke result，沒有 fake suggestion；點擊後 active slot 與信任標頭正確。
- Featured：落地 active slot=`universal:u1`、ready、Demo preview、Version 75、Clinical review pending。
- 視覺證據：`doc/artifacts/llmebm-buyer-demo-phase07-entry-search-2026-07-17.jpg`。

### Phase 1+2+3+4+5+6+7 cumulative gate

- manifest hash 不變；8 個 demo slots、review_pending 狀態與 Phase 6 buyer-readable contracts 都未退化。
- Phase 1–6 receipt hashes 已鎖入 Phase 7 receipt；完整 llmebm/RAG suites 全綠。

### Observed—not hidden

- 目前許多 specialty detail tree（Cardiology 也包含）尚未填 taxonomy；所以本階段提供 Featured + owned search 的真入口，而未宣稱 48 個專科都可下鑽到疾病。
- search 目前是自有 topic/heading search，不是臨床內文全文檢索。
- 未完成產品模組保留可見 disabled 標示；沒有替它們虛構 route。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-07-entry-routes-search.json`

## Phase 8 — Buyer journey, responsive, keyboard and console UAT

狀態：**PASS**（2026-07-17 19:21 +08:00）

### Observable outcome

- 真 Chrome 驗證 desktop 1728×855、tablet 768×900、mobile 390×844；三者 document width 都等於 viewport width，正文無水平 overflow。
- desktop 左右 panes 預設可見；tablet/mobile 預設收合，Show Navigation / Show Resources 可開成 320px overlay，Close controls 可關閉。
- 三種 viewport 都自動落 Overview、顯示 Demo preview/version/review pending 與 draft warning。
- desktop 用 End/Home、mobile 用 ArrowRight/ArrowLeft 切換 Topic/Updates；focus、`aria-selected`、panel hidden 狀態同步。
- browser console errors=0。

### Independent gate

- responsive contracts `2/2`、renderer keyboard/inert self-check PASS。
- Phase 7 完整累積 suite `llmebm 68/68`、`RAG 63/63` 後沒有產品 code 變更；Phase 8 的一次性 viewport harness 已刪除。
- llmebm health=ok、RAG health=healthy。

### DOM + screenshot gate

- tablet DOM：768/768、media query active、兩 pane hidden、兩 restore controls visible、u1 ready。
- mobile DOM：390/390、media query active、兩 pane hidden；Navigation/Resources overlay width 都為 320 且 close 可見。
- 視覺證據：`doc/artifacts/llmebm-buyer-demo-phase08-tablet-mobile-2026-07-17.jpg`。

### Phase 1+2+3+4+5+6+7+8 cumulative gate

- Phase 1–7 receipt hashes 已鎖入 Phase 8 receipt；scope、generation、review、buyer page、entry/search 路徑均未退化。
- desktop + responsive DOM + keyboard + console 四種證據一致，Phase 8 才判 PASS。

### Observed—not hidden

- 系統沒有 `npx`，依 Playwright CLI 技能規則未新增依賴；改用同一真 Chrome 的 same-origin 768/390 layout viewport 做驗收。
- mobile 全域 nav 是水平可滑動設計，未完成模組維持 disabled；不是漢堡選單。
- 本階段不是正式 WCAG conformity audit，也不是臨床人因驗證。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-08-responsive-keyboard-uat.json`

## Phase 9 — Demo operations, restart, reset, errors and backup

狀態：**PASS**（2026-07-17 19:29 +08:00）

### Observable outcome

- 直接沿用 `llmebm.tools.sqlite_backup`，沒有另造備份系統或新增依賴。
- 真實 `topic_content.db` 已建立一致快照，`integrity=ok`，SHA-256 為 `sha256:1f3d507eec91394fc9b04c1a5e55febcfb3de2f48fd00324fede344abd31f63e`。
- 快照先還原到隔離 DB，bytes、integrity 與 SHA 全部一致。
- 真實容器 restart 後 health 恢復，121 slots、8 ready / 20 stale / 93 empty、slot version/workflow 與 manifest hash 全部不變。
- 真實 live reset 依 runbook 執行 stop writer → digest-gated restore → start；還原前 safety backup 與還原後 DB 皆 `integrity=ok`，內容狀態逐字不變。
- Operations runbook 已補齊 buyer demo quick start、restart baseline、snapshot、isolated restore、live reset 與失敗停止條件。

### Independent gate

- 備份檔 737,280 bytes；backup、isolated restore、live restore 與 pre-restore backup digest 相同。
- 錯誤路徑實測：超長 search=400、不存在 slot=404、無 review token mutation=403。
- 三個錯誤請求前後 content status response 逐字一致，live DB SHA 一致，證明 fail-closed 沒有污染 demo。
- SQLite backup/restore 已有的 digest gate、禁止誤覆寫、pre-restore safety backup tests 繼續當作最小資料安全回歸。

### Phase 1+2+3+4+5+6+7+8+9 cumulative gate

- 容器 restart 與 live reset 後 AF `dom_hash` 維持 `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。
- 8 個 demo slots 仍全部 `ready / review_pending`，沒有因備份或重啟觸發生成、stale 或假發布。
- Phase 1–8 receipt hashes 鎖入 Phase 9 receipt；备援與運維演練對前 8 phases 無退化。

### Observed—not hidden

- 這是本機單節點 SQLite demo drill，不是 VM DR、異地備援、HA 或 RPO/RTO 認證。
- 備份於本機 `runtime_reports` 內，未解決正式環境加密、retention 與 access control。
- compose 仍有既有 top-level `version` obsolete 警告，不影響本次 restart/reset PASS。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-09-demo-operations.json`

## Phase 10 — Final cumulative UAT, delivery docs and handoff

狀態：**PASS**（2026-07-17 19:48 +08:00）

### Observable outcome

- Phase 9 restart/reset 後，真 Chrome 重新從 AF canonical route 進入，完整等待 async status/content 後自動落在 Overview。
- 8 個 buyer-demo deep links 逐一驗收：8/8 heading 正確、8/8 Clinical review pending、8/8 draft warning、8/8 有 citations 與 source details。
- Final Management 視覺狀態顯示 Demo preview、Version 100、stored update time、Clinical review pending，且沒有把草稿冒充 published。
- Master Checklist 更新為 125 complete / 51 partial / 61 todo / 4 external，加權完成度 63.5%。
- 已產生下一位 agent handoff，鎖定 live baseline、重現命令、證據、限制與後續順序。

### Independent gate

- Final routes：`/`、AF topic、`/admin`、llmebm health、specialties、stroke search 全部 HTTP 200。
- llmebm health=`ok`；ebm-rag health=`ready`。
- Chrome console entries=0；final screenshot 人工檢視無重疊、無假發布、主窗內容可讀。
- Phase 10 沒有再呼叫 LLM/RAG generation，沒有安裝 dependency，沒有更動醫療 workflow 狀態。

### DOM + screenshot gate

- 8 deep links 的 hash、可見 heading、draft/trust 標示、citation list 與 source-detail list 全部由真 Chrome DOM 取證。
- Management final screenshot：`doc/artifacts/llmebm-buyer-demo-phase10-final-uat-2026-07-17.png`。

### Phase 1+2+3+4+5+6+7+8+9+10 cumulative gate

- llmebm `68/68`，RAG `63/63`，renderer contract PASS，compose config PASS（既有 `version` obsolete warning 除外）。
- Phase 9 真實 reset 後仍為 121 slots、8 ready / 20 stale / 93 empty、manifest hash 不變。
- Phase 1–9 receipts 已鎖入 Phase 10 receipt；Phase 10 的 route health、final screenshot、checklist、runbook、implementation report 與 handoff 均保留 hash。

### Observed—not hidden

- Buyer Demo Phase 1–10 已完成，但完整 llmebm 只有 63.5%；仍有 61 todo、51 partial 與 4 external gates。
- 8 個 demo slots 是 `review_pending`，不是 clinical approved/published；其他 113 slots 仍 stale/empty。
- RAG 本機 venv 已因絕對 Python path 失效，本次在已有 dependencies 的 RAG container 內執行 63 tests；沒有安裝或修理該 venv。
- 正式 reviewer/RBAC、source rights、AF 121-slot 完整性、multi-disease、security/WCAG/load 與 VM DR 仍屬後續工程。

Receipt：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-10-final-delivery.json`

Handoff：`doc/LLMEBM_BUYER_DEMO_PHASE_1_10_HANDOFF_2026-07-17.md`
