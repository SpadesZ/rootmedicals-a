<!--
模組定位: llmebm 核心動態閉環 durable automation 的下一位 agent 交接。
主要責任: 固定結構/evidence 觸發路徑、人工 gate、live 證據、驗收命令與剩餘產品工作。
呼叫來源: 後續 llmebm 施工、事故診斷、demo operator 與 release gate。
輸入契約: 2026-07-18 rootmedicals-a live Docker、topic_content.db、AF manifest 與 Master Checklist。
輸出契約: 可重現 own-page scan → precise stale → durable queue → review gate → bounded generation 的核心閉環。
安全邊界: 不保存 token、PHI、DynaMed cookie/內文/資產；不把 demo workflow 冒充正式臨床發布。
維護提醒: 先讀 Master Checklist；任何 queue/revision/gate 變更都要重跑 unit、Docker、API、DOM＋screenshot。
-->

# llmebm 核心動態閉環 Handoff

## 結論

2026-07-18 已完成「核心概念技術骨架」閉環：

`SQL hierarchy mutation → atomic manifest+scan job → own llmebm DOM+screenshot scan → exact affected slots → durable regeneration queue → current mapping review gate → one-slot RAG/LLM generation → SQLite version → medical review/publish`

Evidence-only 變更不需要重掃頁面：worker 每 300 秒讀 own llmebm status，僅將
`stale_reasons=[evidence]` 的 slot 入隊；同時 structure stale 的 slot 留給 DOM＋截圖 scan，
避免兩條路徑重複工作。開啟疾病頁只讀既有 SQL/manifest/content，不會掃描或呼叫 LLM。

此處的「核心骨架 100%」不代表完整可賣產品 100%。依 Master Checklist，完整產品加權完成度為 **65.0%**。

## 狀態機與人工 gate

1. `scan/regenerate pending → running → completed`：worker 自動執行。
2. transient error：依 persisted attempt ceiling 回 pending；達上限才 failed。
3. worker restart：running automation job 回 pending，再由 atomic claim 重領。
4. manifest 已被後續版本取代：job 進 superseded，不寫舊結構。
5. current source-to-slot mapping 未核准：regenerate 進 waiting_review，**不呼叫 LLM**。
6. reviewer 核准 exact current evidence scope：同一 job 回 pending，自動續跑。
7. waiting 期間 evidence 再變：worker 在 LLM 前比對 revision，舊 job 直接 superseded。
8. LLM output 只成為 draft；仍需 medical review/approve/source gate 才能 publish。

## Live baseline

- llmebm：`http://localhost:33300`；`/api/health` = ok。
- AF topic UID：`topic-e55a50c1a5a4445c`。
- Manifest：121 slots；DOM hash `sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。
- Own-page scan artifact：`llmebm/data/runtime/topic_scans/efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6.json`。
- Expanded screenshot：`llmebm/data/runtime/topic_scans/06fa0d37f8a5df0c30207265643b41344e138dc5e31281569bc613f64cd7c5af.png`。
- Content：9 ready、20 stale、92 empty、0 failed。
- Stale reason split：16 evidence-only、4 structure＋evidence。
- Live reconcile：AF 新增 16 evidence-triggered jobs；全部被 current mapping gate 擋在 waiting_review，API log 全為 409，沒有 LLM acceptance。
- AF automation queue：17 waiting_review（含 reconcile 前既有 1 job）＋1 completed。
- Admin DOM/visual：顯示 `Automatic queue: completed 1 · waiting review 17`、9/9 reviewed mappings current、token 僅以 HttpOnly dev session 保存。

## 主要實作

- `llmebm/app/model/topic_content_model.py`
  - automation queue schema、active dedupe、atomic claim、retry/recover/resume。
  - atomic manifest＋scan enqueue。
  - per-slot `stale_reasons` 與 current manifest listing。
- `llmebm/tools/run_topic_automation_worker.py`
  - own-page DOM＋screenshot scan。
  - affected-only single-slot generation。
  - evidence-only periodic reconcile。
  - mapping waiting/resume、generation terminal tracking。
- `llmebm/main_ebm.py`
  - hierarchy/custom mutation 觸發 scan queue。
  - authorized automation status API。
  - non-force ready-slot no-op 與 mapping approval resume。
- `llmebm/app/static/js/admin_review.js`、`admin.html`
  - automation queue、單 slot regeneration、mapping approval 與 job refresh UI。
- `llmebm/app/static/js/topic_content.js`
  - draft warning 與 approved/review_pending/rejected workflow 一致。
- `llmebm/Dockerfile.scanner`、`docker-compose.yml`
  - Scrapling/browser 依賴隔離 sidecar；API runtime 不背 scanner dependency。

## 驗收命令

```powershell
# 正式完整 llmebm test discovery（必須從 repo root）
.\llmebm\.venv\Scripts\python.exe -m unittest discover -s llmebm\tests -p 'test_*.py'

# Python/JS syntax
.\llmebm\.venv\Scripts\python.exe -m py_compile `
  llmebm\main_ebm.py `
  llmebm\app\model\topic_content_model.py `
  llmebm\tools\run_topic_automation_worker.py `
  llmebm\tools\scan_topic_manifest.py
& 'C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' `
  --check llmebm\app\static\js\admin_review.js
& 'C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' `
  --check llmebm\app\static\js\topic_content.js

# Docker/API
docker compose -f llmebm\docker-compose.yml config --quiet
docker compose -f llmebm\docker-compose.yml ps
Invoke-RestMethod http://localhost:33300/api/health
```

## 已知 ceiling 與下一施工順序

1. reconcile 現為每 300 秒 O(current topics) polling；topic 規模擴大後改 ingestion event/outbox。
2. SQLite atomic claim 可保證本機 sidecar；多 VM/多 worker 前補 lease、heartbeat、DLQ，並評估 PostgreSQL/外部 queue。
3. API generation process crash 後仍會 interrupted；automation job 可重領，但 provider transaction 不做中途續傳。
4. 先處理 waiting_review mapping，再 bounded 生成；不得把 20 stale/92 empty 批量硬填。
5. 完整產品仍需：AF evidence/content completeness、正式 reviewer/RBAC、claim/citation diff、source diversity/conflict/withdrawal、WCAG/localization、security/PHI/SBOM、metrics/cost、multi-disease golden set、VM E2E/DR。

## Repeated-work packaging review

- RootMedicals gap→implementation→multi-layer acceptance：近兩日反覆發生，但已由 `rootmedicals-gap-implementation` skill 覆蓋；本次沿用，不另建重疊 skill。
- DOM＋screenshot＋API＋DB 累積驗收：已固定在 Master Checklist Gate 與本 handoff；目前延伸現有流程即可，不另建 subagent。
- queue 定期監看：產品 sidecar 已自動處理；Codex background automation 會重複且浪費用量，刻意不建立。
- review token 重填：已由 localhost scoped HttpOnly dev session 解決，無需額外 automation。
