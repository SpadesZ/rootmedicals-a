<!--
模組定位: RootMedicals llmebm 從目前狀態到可正式交付的唯一 Master Checklist。
主要責任: 固定完成定義，逐項區分已完成、部分完成、未完成與外部阻塞。
呼叫來源: 後續 llmebm 施工規劃、Phase gate、驗收報告與使用者完成判定。
輸入契約: 使用者 2026-07-15 指定完成定義、live API/DB 現況與永久驗收報告。
輸出契約: 可逐項打勾、不可用局部 demo 冒充完整產品的 P0～P8 清單。
安全邊界: 不保存 DynaMed 內文、登入狀態、cookie、PHI、API key 或第三方資產。
維護提醒: 只有可執行證據通過才能改成完成；每次狀態更新需附日期與證據路徑。
-->

# llmebm 產品完整完成 Master Checklist

基準日期：2026-07-18（核心動態閉環 durable automation 驗收後更新）

## 唯一完成定義

llmebm 只有在以下完整閉環成立時才能稱為「完整做完」：

`自有 SQL 標題結構 → 自有 DOM＋截圖 manifest → LLM planner → RAG evidence → LLM composer → 醫療審核 → 發布 → evidence 更新時 affected-slot 精準 stale／重生`

並且同時符合：

- 正式 runtime 不掃描、不登入、不依賴 DynaMed。
- DynaMed 只在開發期、使用者授權的 session 中作 heading/結構與通用互動研究。
- 醫學 heading 採使用者最新規則：有差異時使用已人工確認的 DynaMed heading wording；不複製文章、圖片、表格、品牌版型或資產。
- SQL、內容、citation、review、publish、security、operations、multi-disease 與 VM gates 全部通過。
- AF 單頁可用、pipeline 可跑、測試全綠，都不能單獨等同完整產品。

## 狀態標記

- `[x]`：已實作且有可執行／live 證據。
- `[~]`：部分完成；已有基礎但尚未滿足完整定義。
- `[ ]`：尚未完成。
- `[!]`：需要使用者／環境／醫療 reviewer 或 VM 資訊才能完成。

## 目前 live 基線

- [x] AF manifest：121 slots，DOM hash `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。
- [x] AF 狀態：9 ready、20 stale、92 empty、0 failed；8 個 buyer-demo slots 為 1 approved draft＋7 review_pending，另 1 個單-slot proof 為 generated，不把 last-known draft 冒充 current ready。
- [x] 自動閉環：16 個純 evidence stale 精準入 durable queue；另 4 個 structure＋evidence stale 留給 DOM＋截圖 scan 路徑，AF live queue 為 17 waiting_review＋1 completed，未核准 mapping 時沒有呼叫 LLM。
- [x] RAG：ready；AF 9 個 paper IDs、177 AF chunks，另有 1 個非 AF fixture chunk。
- [x] 最新 bounded job：`ce872376-ca51-4541-9cea-688f8bf66278`，completed、error=null。
- [x] 100 個 `af-*` reviewed mapping 已建立：54 generation-eligible，46 以 gap/partial/metadata/proprietary 狀態 fail closed；mapping hash `sha256:29433b2cc8c32376c97fd608735f6113658007368ba949da9a93e361ff7e2769`。
- [x] AF 121 slots 均有逐 slot evidence revision；已 live 證明單一 scope 變更只改受影響 revision。
- [x] Browser live gate：desktop 1728×855、tablet 768×900 與 mobile 390×844 的 DOM＋screenshot、導航/資源 overlay、keyboard tabs、draft warning、citation trace 與 source metadata；console 0 error，三種 viewport 均無正文水平溢位。
- [x] Medpilot 單輪 EBM Beta 已接真實 `/api/v1/rag/query`：招呼語不呼叫 RAG；臨床回答的每個 section/source 都必須命中本次 retrieval chunk；索引外問題 fail closed；desktop/mobile DOM＋screenshot、loading、keyboard submit、citation trace 與 console=0 已 live 驗收。

## 目前量化進度

- 2026-07-18 核心動態閉環＋Medpilot 單輪 EBM 問答驗收後：`[x] 133`、`[~] 51`、`[ ] 59`、`[!] 4`。
- 加權完成度：`(133 + 51 × 0.5) / (133 + 51 + 59) = 65.2%`；`[!]` 外部 gate 另列，不用來灌高完成度，live 內容數量也不得用來取代產品完成度。

---

## P0：結構、identity 與生成可靠性

### P0-1 SQL hierarchy schema

- [x] `sidebar_nodes` 已保存 `topic_uid`、`node_key`、`parent_id`、heading/name、`layer_level`、`sort_order`、`source`、`content_target`。
- [x] `(topic_uid, source, node_key)` unique index 已存在。
- [x] `parent_id` foreign key 已開啟，SQL 使用參數化 query。
- [x] AF 舊 21 個 `u*` stable keys 在 121-slot migration 後逐字不變。
- [x] 改 heading／parent／order 後 DB → DOM → manifest 可驗證。
- [x] custom node 新增限制三層；有 children 時 delete 會拒絕。
- [x] `allowed_blocks_json` 已由 SQL row 傳到 sidebar API、DOM dataset 與 manifest；renderer 支援類型仍由安全 allowlist 限制。
- [x] schema 已有 hierarchy lifecycle `status=draft/published/retired`，runtime tree/search 只讀 published rows。
- [x] schema 已有 `created_at`、`updated_at` 與 checksum-locked `schema_migrations` version receipts。
- [x] 通用 hierarchy preview/apply/rollback API 已支援 rename/reorder/move/retire/restore，並以 optimistic before-hash 防止覆寫並行變更。
- [x] hierarchy integrity service 禁止 cycle、orphan、非法 level、跨 topic/source parent 與非法 source/status。
- [x] hierarchy transaction 與 audit log 保存 actor、comment、before/after snapshot/hash、時間與 rollback 關聯。

### P0-2 SQL 成為唯一 source of truth

- [x] runtime tree 會從 SQLite `sidebar_nodes` 讀取。
- [x] AF 121 headings、parent、level、order 已存在 SQL，且正式頁面不依賴 DynaMed。
- [x] production Python 已無 `UNIVERSAL_TEMPLATE`；generic 21-slot template 由 `001_condition_hierarchy_template.sql` 保存。
- [x] production Python 已無 `ATRIAL_FIBRILLATION_REFERENCE_TAXONOMY`；AF 121-slot seed 由 `002_atrial_fibrillation_hierarchy.sql` 保存。
- [x] universal、AF taxonomy 與 reviewed heading correction 已拆成 3 個 immutable、checksum-locked SQL artifacts。
- [x] production restart 不再用 Python taxonomy 新增／校正既有 SQL rows；post-migration SQL edit restart-preserve test 已存在。
- [x] 移除 Python constants 後，fresh seed、production copy、live restart 的 DB→DOM→manifest 均維持 121 slot identity 與 hash。

### P0-3 結構變更 affected-slot stale

- [x] overall manifest hash 保留。
- [x] 每個 slot 有 `slot_structure_revision`。
- [x] 無關 slot 結構變更不會讓整頁 stale。
- [x] heading/heading_path 改變只影響該 slot 與實際 contract 改變的 descendants。
- [x] reorder 不改臨床 slot contract 的測試已存在。
- [x] hierarchy retire/restore 使用 soft lifecycle，測試確認可還原且不重配 slot identity／不刪內容歷史。
- [ ] 補 retire/restore Admin UI 與發布前視覺 preview/diff。

### P0-4 Planner／composer reliability

- [x] Planner schema validator 與 21-slot JSON truncation bounded fallback 已完成。
- [x] fallback 每批最多 6 slots，不猜補截斷 JSON。
- [x] Composer 只接受 evidence bundle，citation 必須命中 supplied `(paper_id, chunk_id)`。
- [x] correction retry 會帶前次 assistant JSON 與 validator error。
- [x] schema-invalid 兩次後安全回 `insufficient_evidence`，不放寬 validator。
- [x] Gemini `STOP` 但無 text parts 只重試一次。
- [x] safe renderer 使用 `createElement/textContent`，不執行模型 HTML。
- [~] provider structured-output／JSON Schema 尚未成為所有 provider 的統一 contract。
- [~] output-shape diagnostics 有 error/fallback reason，但尚未形成完整 observability schema 與 dashboard。
- [x] Composer 只接受 exact root 或已證實的單層 `content` envelope；其他 wrapper fail closed，已有 fixtures。
- [ ] 補 planner/composer token、latency、retry、finish reason metrics。

### P0-5 AF pilot 完成 gate

- [x] AF 121-slot heading hierarchy、DOM manifest、expanded screenshot 與 browser flow 已建立。
- [x] 13 stale 已受控嘗試；使用 bounded receipts，不做 121-slot 無界生成。
- [x] 8 個 buyer-demo ready slots 合計 18 blocks、46 citations、7 unique papers；全部 citation 命中 reviewed paper_id/chunk_id，缺 citation=0。
- [x] preserve-last-ready：新 insufficient 不覆蓋舊 ready。
- [~] Buyer-demo bounded generation 後 live 為 8 ready、20 stale、93 empty；舊 draft 仍可供內部預覽但不是 current/published。
- [~] AF 9 paper IDs／177 AF chunks 仍主要源自同一份 ACC/AHA/ACCP/HRS guideline，來源多樣性不足。
- [x] 100 個新 `af-*` keys 已逐一保存 reviewed evidence-set mapping；54 covered 可生成，46 明確 fail closed。
- [x] bounded retry 只在 evidence revision 改變後執行；沒有把 insufficient 或 stale 手動升為 ready。
- [~] 54 個 covered slots 尚受來源商用授權與內容生成成本 gate；其餘 slots 已列 gap/partial/metadata/proprietary 原因。
- [ ] AF 最終 gate：0 failed、0 empty、0 不合理 heading、0 無 citation、0 未解釋 stale。

---

## P1：DynaMed 開發期參考研究與匯入

### P1-1 授權 scanner/capture

- [x] 曾使用使用者登入並授權的 Chrome session 展開 AF Topic Menu。
- [x] AF capture 覆蓋 121 headings、12 roots、最深 4 層。
- [x] capture 不保存 cookie、localStorage、password、session token 或文章內文。
- [x] 自有頁面使用 DOM＋screenshot scanner，保留 viewport、hash 與 state。
- [~] DynaMed 來源端曾以 Chrome 搭配 screenshot/hash 做有限 page-family 視覺研究，但 raw reference screenshots 已依安全／版權邊界清理；現存證據不能證明 121 個節點與所有互動狀態都逐一 screenshot-paired。
- [ ] 在下一次使用者授權的 reference audit 補 AF source-side page/state/viewport matrix：全展開 heading DOM、tabs/disclosure、全域導覽與代表 responsive viewport；保存 sanitized DOM 結構、每狀態 screenshot hash 與人工 review receipt，raw screenshot 驗收後依政策清理。
- [~] 目前只完成 AF 與有限 page family，不代表所有 DynaMed page patterns。
- [ ] 選取多種疾病代表頁，人工授權後補 page-family/responsive state matrix。

### P1-2 只取結構規律

- [x] 只保留 heading、parent、level、order、generic interaction/state 規律。
- [x] 沒有把 DynaMed 文章、圖片、表格、品牌資產或 raw HTML 寫進 repo。
- [x] llmebm layout 是自有三欄設計；只有抽象規律相近。
- [x] raw reference artifacts 在驗收後已清理。
- [ ] 對後續每次 reference capture 執行相同 purge/receipt gate。

### P1-3 清理、人工決策、匯入

- [x] 每個 AF heading 有穩定 node_key，既有 21 keys 被保留。
- [x] 使用者已覆寫早期「中性改名」方案：醫學 headings 有差時使用人工確認的 DynaMed wording。
- [x] capability-only reference comparison/import receipt 已完成且冪等。
- [~] 現有 `reference_structure_import.py` 只記錄 capability audit，不是通用 taxonomy importer。
- [x] AF 121 taxonomy 已由 immutable、checksum-locked SQL migrations 保存；production Python 不再持有 taxonomy constant。
- [x] generic hierarchy change 已有 preview、diff、transaction、rollback 與 audit backend；尚未有獨立 approve role/UI。
- [~] stable slot IDs 與 DB→DOM→manifest 已有 transaction tests；每批 import 的 browser screenshot approval UI 尚缺。

### P1-4 Runtime independence

- [x] 正式 llmebm runtime 不掃 DynaMed。
- [x] 疾病頁開啟只掃／讀自己的 SQL、DOM、manifest、RAG 與內容 DB。
- [x] DynaMed 改版不會直接讓正式頁失效；只有人工重新研究時才更新自有結構。
- [~] fail-closed source policy、Master Checklist 與 operations runbook 已記錄 runtime independence；正式法律核准版 copyright policy 尚缺。

---

## P2：Topic／template／hierarchy 管理系統

### Topic lifecycle

- [x] canonical topic slug 與 deterministic `topic_uid` 已存在。
- [x] AF/Atrial Fibrillation 類 URL/display variants 可 canonicalize。
- [~] manifest 有 `template_version` 欄位，但沒有完整 template registry/lifecycle。
- [ ] 正式 topic table：create、rename、alias、archive、restore、soft delete。
- [ ] Specialty → group → topic 導航一致性與 duplicate-topic detection。
- [~] hierarchy backend 已有 preview/diff/apply/rollback/audit；獨立 draft version 與 approve role/UI 尚缺。
- [ ] hierarchy JSON/CSV import/export 與 schema validation。

### Hierarchy Admin

- [x] custom sidebar add/delete 與三層限制仍保留，generic hierarchy mutations 另有獨立 token。
- [~] 通用 rename/reorder/move/retire/restore backend 已完成；drag UI 尚缺。
- [ ] 顯示 parent path、node_key、slot_id、content status。
- [ ] 顯示每個 slot 使用的 RAG sources 與 evidence revision。
- [ ] 顯示結構變更預計影響的 stale slots。
- [ ] 發布前 DOM＋screenshot preview 與 visual diff。
- [x] hierarchy admin mutations 保存 actor、時間、comment、before/after snapshot/hash 與 rollback_of。

---

## P3：醫學內容審核與發布生命週期

### 已有基礎

- [x] `topic_content_versions` 保存逐 slot 版本、plan、content、query、evidence、model、error、current flag。
- [x] current 只有一筆的 unique index 已存在。
- [x] ready/insufficient/failed 與 computed stale 可區分。
- [x] 新 evidence/structure 變更不會直接刪除舊內容。
- [x] citation schema 與 evidence-only composer gate 已存在。

### 正式醫療 workflow

- [x] 內容 workflow 狀態：generated、review_pending、approved、published、rejected、retired；stale 由 current revision 動態計算。
- [x] LLM 內容只進 generated draft，未經 review/approve/source gate 不得 published。
- [~] review audit 保存 actor、時間、意見、before/after 與 source-gate receipt；正式 reviewer identity/RBAC 尚缺。
- [ ] before/after content diff、claim diff、citation diff。
- [~] backend 會保留並優先提供最後 published 版本；published stale 的完整信任標頭/警告 UI 尚缺。
- [ ] 單一 claim approve/reject/edit，不必整 slot 重做。
- [~] source policy/lifecycle digest 會讓受影響 slots stale；claim-level withdrawal 標記 UI 尚缺。
- [x] rollback 至先前 published 版本會重新驗 evidence/structure revision 與 commercial source gate，並寫 audit。
- [!] 需要正式醫療 reviewer 與發布責任規則。

---

## P4：RAG corpus、retrieval 與 evidence refresh

### Ingestion／metadata

- [x] 現有 AF corpus：9 個 AF paper IDs、177 AF chunks；prepare `--verify-only` 全部 exact、0 updates。
- [x] metadata 支援 topic_key、slot_keys、paper_id、chunk_id、source type、6S、OCEBM、guideline flag。
- [x] bundled guideline 的舊 `u*` slots 有人工審核 scope mapping。
- [x] 新 100 個 `af-*` slots 均有 reviewed mapping 狀態；54 covered、46 fail closed，禁止 keyword/LLM 猜 scope。
- [~] 目前 9 個 AF paper IDs 主要是同一 guideline 的受控 slices，來源類型與機構多樣性不足。
- [ ] 支援並驗收 guideline、systematic review、drug label、clinical policy。
- [~] AF source policy 已保存 DOI、year、organization、source URL、document version、license/provenance；尚未推成所有 ingestion 的全域必填。
- [~] source policy 支援 current lifecycle、document version 與 commercial use gate；完整 replacement/expiry/retraction scheduler 尚缺。
- [~] AF native-text slices 有 exact/idempotent quality gate；視覺/表格 OCR 與人工 reject path 尚缺。
- [x] source 使用權 fail closed：9 個 AF IDs 皆 `permission_required`、commercial publication=false，未獲書面許可不得發布。

### Retrieval

- [x] Topic planner 的 evidence needs 會進 slot-specific retrieval queries。
- [x] strict metadata + bounded fallback 與 no-hit insufficient 基礎已存在。
- [x] retrieval/composer 不得以模型自身醫學知識補洞。
- [x] retrieval log 保存 query、slot、hits 與 compose fallback reason。
- [~] query plan 與 top hits 可稽核，但 UI 未完整顯示 excluded reasons。
- [ ] evidence conflict detector：衝突時不得硬合成單一結論。
- [~] retrieval 強制 current source lifecycle 並把 source-policy digest 納入 revision；diversity、supersession 與 jurisdiction policy 尚缺。
- [ ] reviewer 可查看 included/excluded hits 與理由。

### Evidence refresh

- [x] `u*` slots 已驗證 affected-only evidence revision/stale/regeneration。
- [x] evidence 更新不需要重掃 DynaMed。
- [x] 全 121 slots 均能取得逐 slot evidence revision；`af-*` 不再使用 corpus-wide digest。
- [x] ingestion 先載入並驗證 reviewed affected-topic/slot mapping，未知或不完整 mapping fail closed。
- [x] live 證明 scope 變更只改受影響 slot revision；worker 每 300 秒 reconcile evidence-only stale，active dedupe 後只把 affected slots 放入 durable queue。
- [ ] evidence revision diff UI、人工「立即更新」、source withdrawal refresh。

---

## P5：完整前端產品

### 已完成 Topic UX

- [x] 121-slot hierarchy 與 disclosure。
- [x] Find in Topic、Previous/Next、stable hash deep link。
- [x] Topic／Updates 真實 tabs。
- [x] References drawer 與 citation dedupe。
- [x] recommendation strength/certainty 有證據才顯示，否則 not stated。
- [x] Related Topics/sections。
- [x] Patient Information 空狀態與導航。
- [x] global topic/heading search。
- [x] Follow/Alerts local state、Print/Cite/Share 基礎功能。
- [x] safe component renderer、stale/insufficient/empty 狀態與 browser console=0。
- [x] Medpilot 單輪 EBM Beta：真實 RAG／LAVA 生成、逐段可追溯 citation、索引外問題 fail closed、無模型 HTML 注入；loading 與 Retry/timeout/offline UI contract 已實作，其中正常回答與 insufficient-evidence 分支已 live 驗收。
- [x] fake Images/Tables tabs 已移除，不顯示假內容。

### 尚缺完整產品 UI

- [ ] Images tab 接合法授權的自有/公開 assets。
- [ ] Tables tab 接結構化 evidence table renderer。
- [x] citation source detail panel 可見 paper/chunk、organization、publication year、DOI、document version、source status、license status 與 commercial-publication decision；只接受當前 slot 已引用 paper IDs，回應欄位由 public allowlist 限制。
- [~] strength、updated time、publication year 與 source status 已顯示；jurisdiction 尚未納入 source policy/detail schema。
- [~] Topic trust header 已顯示 Demo preview、內容版本、stored updated time 與 `Clinical review pending`；真實 medical reviewer/editor、disclosure/conflict 與發布責任仍缺，且不得使用虛構名稱。
- [ ] Evidence/Study Details：study design、population、intervention/comparator、outcome、effect、certainty/limitations 與 included/excluded 理由，全部可追到 paper/chunk。
- [~] Updates 已逐 slot 顯示 status、workflow、version 與 stored time；added/changed/retired 差異摘要、evidence revision、reviewer 與 published time 仍缺。
- [ ] 受控 feedback/report-issue flow：可回報內容錯誤、過期證據、citation 問題與 UI 問題，進入可稽核 queue；不得在回報中收 PHI。
- [~] backend workflow/audit API 與 Medical Review Admin queue、狀態 filter、draft/published comparison、source gate、audit、submit/approve/reject/publish/rollback UI 已完成；正式 reviewer identity/RBAC、claim/study diff 與 production review-token 配置尚缺。
- [x] Medical Review Admin 可由 UI 選擇單一 empty/stale/failed slot 啟動受控重生；review token 不取得 generation token，後端強制 non-force、current mapping review 與單-slot gate，並可手動刷新 generation/automation queue 狀態。
- [~] global search 目前以 topic/heading 為主；全文內容 search 尚未完整。
- [~] Topic 與 Medical Review Admin 的 desktop/768px tablet/390px mobile Chrome DOM＋screenshot 已驗，pane overlay/ARIA/keyboard tabs/無溢位、source detail 與 review action gating 通過；screen-reader 與完整 device matrix 尚缺。
- [~] Medpilot 目前只支援單輪、無 patient context／對話記憶／串流；timeout、offline、Retry 分支已有程式與 contract，但尚未做 live fault injection。390px widget 本身無裁切，首頁既有導覽／專科 grid 仍有水平 overflow，另列前端修正。
- [ ] contrast/WCAG、focus order、zoom、reduced motion 驗收。
- [~] Print/Cite/Share 有基礎；正式 PDF view/export 尚缺。
- [ ] 中文／英文 UI 與醫學內容 language strategy。
- [~] latest generation job 狀態與錯誤可由 Admin 手動刷新；retry/cancel、階段式 progress、offline/RAG outage recovery UI 尚缺。
- [ ] Specialty navigation 與 multi-disease product flow 完整化。
- [ ] 定義 llmebm 自有全域產品導覽 IA；不得以 DynaMed 導覽列逐項複製充當產品規格。
- [x] index/topic 公開頁的 `href="#"` 已清零；可用入口連到真實 route，未完成的全域模組明確 disabled。
- [~] 錯誤英文 `Latest Publishments` 已改為 `Latest Publications`；該全域模組仍 disabled，尚未接上真實功能。
- [ ] `Drug Library`、`Chemo Regimens`、`RootMed-Evaluators/EBM Tools` 接上真實模組、權限與 empty/error state。
- [ ] 頂部 `Calculators` 從不可點擊文字改為 deterministic calculators 真實入口；臨床計分不得交給 LLM 自由計算。
- [ ] 定義 llmebm/RootMed-Evaluators 最小 calculator catalog；每個公式保存來源、版本、適用族群、單位、input bounds、edge-case tests 與更新責任，不以複製 DynaMed 全目錄為目標。
- [ ] 決定並實作全域 `Patient Information` 與 global Alerts/Updates 的瀏覽入口；若刻意只保留 topic context，需在 IA 與驗收文件明確記錄。
- [ ] 全域導覽完成 desktop/tablet/mobile、keyboard、active state、deep-link、404 與權限驗收。

---

## P6：安全、法規與商業品質

### 已有安全基礎

- [x] generation endpoint 限 loopback 或 topic token。
- [x] scanner origin allowlist、path escape/hash validation。
- [x] scanner 不讀 cookie/localStorage/history。
- [x] XSS/malicious renderer contract；模型 HTML 不執行。
- [x] citation 必須命中 supplied evidence bundle。
- [x] key/error redaction 基礎與 API key 不回傳規則。
- [x] DynaMed 只作結構研究、不作 runtime source 的技術邊界已在驗收報告記錄。

### 尚缺正式安全／法規 gate

- [~] review 與 hierarchy mutations 使用彼此獨立、必填且 constant-time 比對的 token；正式 reader/editor/reviewer/admin 登入、session 與 RBAC 尚缺。
- [~] mutation API 不使用 cookie auth 且要求 header token；正式登入後仍需完整 CSRF policy/test。
- [~] 已有每 client/path 30 requests/60s 的本機 mutation ceiling 與 429；distributed limit、abuse policy、generation quota 尚缺。
- [ ] topic token rotation、expiry、scope 與 revocation。
- [ ] production provider keys 使用 secret manager，不只環境變數。
- [x] Topic generation schema 只接受 bounded slot IDs、top_k 與 allowlisted evidence filters，不接受任意病人敘述欄位。
- [ ] screenshot PHI detector／block policy。
- [~] scanner SSRF allowlist 已有；所有 ingestion/fetch URL 的統一 SSRF policy 尚缺。
- [~] prompt injection/XSS 有測試；malicious PDF/OCR、dependency supply-chain matrix 尚缺。
- [~] hierarchy/review audit log 已有；security event log 與不可竄改保存尚缺。
- [x] AF source license/provenance fail closed，DynaMed 僅開發期結構研究且不進 runtime 的書面規則已固定。
- [ ] medical disclaimer、適用範圍、reviewer 發布責任。
- [ ] 對外 Help、Privacy、Terms、EBM/editorial methodology、support/status 頁面與版本化政策連結。
- [ ] data retention/deletion policy、backup encryption、access control。
- [ ] dependency/container/SBOM/vulnerability scan 與修補 SLA。

---

## P7：Runtime、維運與擴展

### 已有基礎

- [x] bounded CLI runner：每批 ≤6、single active job、receipt、explicit slot scope。
- [x] process restart 時 pending/running jobs 會標成 interrupted。
- [x] 1800 秒 Topic transaction timeout。
- [x] provider network/429/5xx retry 基礎與 Gemini empty STOP 單次 retry。
- [x] health/readiness、slot status counts 與 job status API。
- [x] AF corpus prepare/rebuild 工具與 verify-only gate。

### 尚缺正式 runtime

- [x] SQLite durable automation queue、原子 claim、active-job dedupe、persisted retry ceiling 與 restart recovery 已完成；sidecar 可與 API writer 共用 DB 而不誤中斷 generation job。
- [x] structure scan 與 evidence-only stale 都由同一 worker 轉成 bounded single-slot regeneration；mapping 未核准時持久停在 waiting_review。
- [ ] 多 worker lease、heartbeat、dead-letter queue 與 PostgreSQL/外部 queue 評估。
- [~] automation job 有 evidence/manifest/slot idempotency key 與 active 重送去重；一般 generation batch 的全域 idempotency 尚缺。
- [~] explicit single-slot retry 可做；cancel/pause/resume API 尚缺。
- [ ] job retry policy：provider、RAG、schema、evidence gap 分類處理。
- [ ] provider circuit breaker、quota/rate window、fallback policy。
- [ ] token/cost accounting per job/slot/provider。
- [ ] planner/retrieval/composer latency、success/failure/stale metrics。
- [ ] structured logs schema，明確禁止 secrets/PHI/raw prompt。
- [~] SQLite 原生一致 backup、integrity_check、SHA-256 restore gate、pre-restore safety backup 已完成；live `topic_content.db` 已停 writer 實際 restore 後恢復 health/8 ready，scheduled retention/encryption 尚缺。
- [ ] multi-user/multi-instance 前評估 PostgreSQL。
- [ ] formal schema migration versioning/up/down policy。
- [~] 本機 buyer-demo quick start/restart/snapshot/isolated restore/live reset runbook 與實際 drill 已完成；VM RPO/RTO、排程與 evidence-index rebuild drill 尚缺。
- [x] own-page scanner 已改接 bounded durable queue＋current evidence-scope gate；每次只送一個 affected slot、force=false，不在 page open 時掃描或呼叫 LLM。

---

## P8：正式驗收、multi-disease、VM 與交付

### 已有測試／證據

- [x] stable slot identity、manifest revision、scanner idempotence、screenshot hash/path escape。
- [x] planner truncation、composer schema/citation、affected-only stale、preserve-last-ready。
- [x] malicious renderer、browser desktop user flow、citation trace。
- [x] AF 121 heading hierarchy、runtime independence 與 bounded receipts。
- [x] 永久 verification report 已存在。
- [x] `_TEMP_LLMEBM_DYNAMIC_TOPIC_IMPLEMENTATION_DRAFT_2026-07-10.md` 已在使用者 2026-07-15 明確驗收後刪除；不再把它列為待刪項。

### 每個施工包固定驗收協議（程序 Gate，不計入完成度）

- `[Gate]` 先列 requirement → code/DB/API/DOM surface → test case 對照，不用單張 screenshot 代替功能驗證。
- `[Gate]` 後端跑最小 contract/unit/integration、migration/idempotence、錯誤與 rollback/preserve 測試；涉及 RAG 時另驗 source/chunk/citation/affected-slot trace。
- `[Gate]` UI 必須在 live browser 跑真實點擊、輸入、keyboard、loading/empty/stale/error/ready、deep-link/reload 與 console error 檢查。
- `[Gate]` 同一驗收狀態同時保存 DOM 語意證據與 screenshot；DOM 驗功能/ARIA/state/slot identity，screenshot 驗 overflow、遮擋、層級、密度、對齊與 responsive。
- `[Gate]` 視覺比較以 llmebm 自有核准 baseline／設計規則為準，不以 DynaMed pixel-perfect 複製為驗收目標。
- `[Gate]` 小施工包驗 changed surface＋critical smoke；Phase/release gate 跑完整 page-family、疾病 golden set、viewport、權限、失敗復原與 VM E2E matrix。
- `[Gate]` 驗收報告固定列已實作、未做、測試結果、screenshot/DOM artifact、blocker、完成度與下一施工順序；未跑的 case 必須明列 skipped，不能算 PASS。

### 尚缺測試矩陣

- [x] SQL migration、rename/reorder/move/cycle/retire/restore/rollback 與 stable slot identity 均有測。
- [x] generated→review→approve→publish、stale fail closed、preserve/rollback published version 與 audit 有測。
- [~] provider/RAG failure 有部分 contract；完整 outage/recovery/circuit-breaker 尚缺。
- [~] automation worker restart 可重領 running queue job，且不會中斷 API generation；API generation process 本身 crash 後仍標 interrupted，尚未做到 provider transaction 中途續傳。
- [~] desktop/768px tablet/390px mobile browser DOM＋screenshot、overlay、ARIA、keyboard deep-link、citation source detail 與 console 已驗；完整 WCAG/device matrix 尚缺。
- [ ] performance、large topic、concurrency、multi-worker、load test。
- [~] token、rate limit、source gate、XSS/path/hash tests 已有；RBAC、CSRF、screenshot PHI、malicious PDF、dependency scan 尚缺。
- [~] existing production DB migration/restart 與本機 live stop/restore/start reset drill 已通過；VM rollback drill 仍未做。

### 疾病 golden set

- [x] AF 作為 Cardiology pilot。
- [ ] 常見內科疾病。
- [ ] 急症。
- [ ] 腫瘤。
- [ ] 感染症。
- [ ] 兒科或婦產科。
- [ ] 大量藥物/guideline 疾病。
- [ ] 證據稀少且應回 insufficient 的疾病。

### VM／production delivery

- [x] VM profile 有 `# CONFIRM` 時 fail closed，不覆寫正式環境。
- [!] 取得 VM 內部 service DNS/ports/compose topology。
- [!] 確認 llmebm→ebm-rag、llmxx-server→RAG/LAVA connectivity。
- [!] TLS termination、secret ownership、rotation、backup path、monitoring。
- [ ] VM health/readiness、browser E2E、restart、rollback、backup restore。

### 文件

- [~] README、永久 verification report、RAG handoff 文件已有部分內容。
- [~] hierarchy/content/review/publish code contracts、tests 與本清單已有；正式 schema reference 尚缺。
- [ ] Topic/Hierarchy Admin 手冊。
- [~] AF mapping/source-policy config、tests 與官方 rights links 已固定；通用 ingestion/licensing 手冊尚缺。
- [ ] medical reviewer 手冊。
- [~] `LLMEBM_OPERATIONS_RUNBOOK.md` 已涵蓋本機 stop/backup/verify/restore/rollback；VM topology、加密、retention、RPO/RTO 尚缺。
- [ ] 正式 release verification report 與使用者最終驗收。

---

## 從現在開始的強制施工順序

1. **[已完成] SQL 唯一 source of truth**：schema、versioned migrations、Python taxonomy 移除、121 slot/DOM/內容不變 gates 已於 2026-07-16 通過。
2. **[已完成] 新 100 `af-*` affected-slot mapping**：54 covered、46 明確 fail closed，不用 LLM/heading keyword 猜。
3. **[目前最高優先／外部授權約束] AF targeted evidence/content completion**：只生成 scope 與 source rights 都允許的 slots；現行 commercial publication gate 全部拒絕。
4. **[backend＋基礎 UI 已完成] medical review/publish lifecycle**：下一步補正式 reviewer identity/RBAC、claim/study diff、production review-token 與正式 reviewer UAT。
5. **[backend 已完成] Topic/Hierarchy Admin**：下一步補 draft/approve role、drag UI 與 screenshot preview/diff。
6. **[部分完成] RAG source lifecycle**：已有 version/current/license/use-gate；下一步補 withdrawal、conflict、diversity、supersession。
7. **補前端 Images/Tables、review claim/study diff、完整 accessibility/localization**；source detail 與基礎 Medical Review Admin 已完成。
8. **補 security/RBAC/PHI/secret/distributed rate-limit/security audit/vulnerability gates**。
9. **改 durable queue、metrics、cost、scheduled backup/retention、VM DR**；本機 SQLite backup/restore tool 與 drill 已完成。
10. **擴充 multi-disease golden set**。
11. **完成 VM E2E、runbooks、正式 verification 與使用者最終驗收**。

## 禁止錯誤宣稱

- 不得因 AF page 可開啟就說 llmebm 完成。
- 不得因 121 headings 完成就說 121 clinical slots 完成。
- 不得把 stale 的 last-known-good 說成 current ready。
- 不得把 insufficient 手動升成 ready。
- 不得在 `af-*` precise scope=0 時批量生成 100 empty。
- 不得因 SQL 已有 rows 就說 SQL 是唯一 source of truth；Python seed/constants 移除前只能說部分完成。
- 不得把 DynaMed 當 runtime RAG source、定期 crawler 或正式依賴。
- 不得在 medical review/publish、security、multi-disease、VM gates 未完成時宣稱「能賣等級」。
- DynaMed CME、品牌合作、Mobile Apps 與其商業帳號體系不是 llmebm 完成必要條件；除非使用者另立需求，不得為了表面相似擴張範圍。
