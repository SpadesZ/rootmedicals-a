<!--
模組定位: llmebm Buyer Demo Phase 1–10 的下一位 agent 實作交接。
主要責任: 鎖定當前 live baseline、驗收證據、重啟方式、未完優先序與禁止錯誤宣稱。
呼叫來源: 後續 llmebm 施工 agent、reviewer、demo operator 與交付驗收。
輸入契約: rootmedicals-a 當前 dirty worktree、Phase receipts、Master Checklist 與本機 Docker runtime。
輸出契約: 可重現 8-slot AF buyer demo 與一個單-slot 動態重生證據，並對 61 個未完、51 個部分完成項保持誠實。
安全邊界: 不保存或輸出 secret、PHI、DynaMed 內文/cookie/第三方資產；不偽造 clinical approval。
維護提醒: 任一後續變更必須先讀 Master Checklist，單 phase 實測後再做累積回歸。
-->

# llmebm Buyer Demo Phase 1–10 Handoff

## 交接結論

Buyer Demo Phase 1–10 已逐階段實作與驗收通過。這代表「可現場展示的限定 AF demo」，
不代表完整 llmebm 產品或正式醫療發布已完成。Master Checklist 當前為
`125 complete / 51 partial / 61 todo / 4 external`，加權完成度 **63.5%**。

## 當前 live baseline

- Repo：`C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a`
- llmebm：`http://localhost:33300`，`/api/health` = `ok`
- AF demo：`http://localhost:33300/topic/atrial-fibrillation`
- Medical Review Admin：`http://localhost:33300/admin`
- ebm-rag：`http://localhost:33301/api/v1/rag/health` = `ready`
- AF topic UID：`topic-e55a50c1a5a4445c`
- Manifest：121 slots，`dom_hash=sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`
- Content：9 ready / 20 stale / 92 empty / 0 failed
- 8 demo slots：全部 `review_pending`；另有 1 個 regeneration proof slot 為 `generated`，0 published
- 不得在現場 demo 前重跑全頁生成。

## Phase 10 後核心動態閉環證據

2026-07-17 以 Admin 的單-section regeneration 控制完成一次真實閉環：

- Slot：`topic-e55a50c1a5a4445c:universal:af-mgmt-ablation-rhythm`
- Heading：`Ablation Therapy for Rhythm Control`
- 精準 mapping：`RM_AF_RHYTHM_CONTROL_2023`，current mapping review 已核准
- 生成 gate：只接受一個 slot、`require_current_scope_review=true`、未使用 force
- Job：`ce872376-ca51-4541-9cea-688f8bf66278`，結果 `completed`，無 error
- 結果：`empty → ready`、Version `101`、workflow `generated`
- 引用：`RM_AF_RHYTHM_CONTROL_2023:chunk:000019`
- 隔離性：其餘 120 個 slot 的 status/version 變更數為 `0`
- 累積回歸：llmebm `19/19`、RAG `51/51`，共 `70/70`
- DOM＋視覺：heading、摘要、paper/chunk citation、Version 101 與三欄版面均通過
- Dev Admin auth：開啟 localhost `/admin` 會建立 7 日 HttpOnly、SameSite=Strict、API-path-scoped cookie；token 不進 HTML/JS，LAN Host 不啟用
- Dev auth 回歸：有 cookie 可讀 121-slot review queue，無 cookie 仍為 403
- 共用 About：Home、Topic、Admin 使用同一個原生 modal dialog；實際點擊、關閉、焦點復原與視覺遮罩通過
- llmebm host contracts：`72/72`

此證據代表 `SQL/manifest → reviewed mapping → RAG retrieval → LLM compose →
slot persistence → Topic DOM` 的核心技術骨架已跑通；不代表 121 slots 已填滿、
內容已通過真實醫師 review，或產品已達 production/commercial 完成。

## Phase 結果

| Phase | 結果 | 主要交付 |
|---|---|---|
| 1 | PASS | scope freeze、唯一 completion gate |
| 2 | PASS | reviewed scope provenance、append-only decision |
| 3 | PASS | precise source→slot mapping、affected-slot stale |
| 4 | PASS | bounded generation，8 個 core slots ready |
| 5 | PASS | review_pending lifecycle、Admin Demo Preview，無假醫師簽核 |
| 6 | PASS | buyer-readable AF page、trust header、Updates、source details |
| 7 | PASS | Featured/search 真入口、canonical route、無公開 `href="#"` |
| 8 | PASS | desktop/tablet/mobile、overlay、keyboard、console 0 error |
| 9 | PASS | backup、isolated restore、live reset、restart、fail-closed errors |
| 10 | PASS | final 8-slot Chrome deep-link UAT、routes/health、checklist、handoff |

## 8 個 demo slots

- `universal:u1` — Overview and Recommendations
- `universal:u2-1` — Description
- `universal:u3` — Diagnosis
- `universal:u3-1` — Making the Diagnosis
- `universal:u4` — Management
- `universal:u5-2-1` — United States Guidelines
- `universal:af-mgmt-thromboembolic` — Thromboembolic Prophylaxis
- `universal:af-prognosis-stroke` — Embolic Stroke and Thromboembolism

最終 Chrome 對 8 個 deep links 逐一驗收：heading 8/8、draft warning 8/8、
Clinical review pending 8/8、citation/source details 8/8、console entries=0。

## 驗收與重現

```powershell
# llmebm 72 tests
.\llmebm\.venv\Scripts\python.exe -m unittest discover -s llmebm/tests -p 'test_*.py'

# renderer contract；系統 PATH 沒有 node，使用 Codex bundled Node
& 'C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' `
  'llmebm/tests/topic_content_renderer_check.js'

# compose contract
docker compose -f llmebm/docker-compose.yml config --quiet
```

RAG 63 tests 在有完整 dependencies 的 `ebm_rag_web_ui` 內通過。當前 compose 不掛載
`ebm-rag/tests` 與 `ebm-rag/tools`，本次將 host tests 複製到容器 `/tmp` 後執行；
此是驗收方式，不是正式 test image。後續若要固定 CI，應做獨立 test image/mount，
不要在 runtime image 安裝額外套件。

## Demo 啟動、備份與 reset

完整步驟見 `doc/LLMEBM_OPERATIONS_RUNBOOK.md`。最小啟動：

```powershell
docker compose -f llmebm/docker-compose.yml up -d llmebm-standalone
Invoke-RestMethod http://localhost:33300/api/health
```

Phase 9 可回復快照：
`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-09-topic-content-backup-20260717T192543.db`
。SHA-256 為
`sha256:1f3d507eec91394fc9b04c1a5e55febcfb3de2f48fd00324fede344abd31f63e`。
還原 live DB 前必須停 writer，必須指定 expected digest，且必須保留 pre-restore backup。

## 證據索引

- 實作/驗收紀錄：`doc/LLMEBM_BUYER_DEMO_IMPLEMENTATION_2026-07-16.md`
- 唯一完成清單：`doc/LLMEBM_PRODUCT_COMPLETION_MASTER_CHECKLIST.md`
- 維運手冊：`doc/LLMEBM_OPERATIONS_RUNBOOK.md`
- Phase receipts：`runtime_reports/llmebm-buyer-demo-2026-07-16/phase-01-*.json` 至 `phase-10-*.json`
- Final visual：`doc/artifacts/llmebm-buyer-demo-phase10-final-uat-2026-07-17.png`
- 單-slot 動態閉環 visual：`doc/artifacts/llmebm-single-section-regeneration-v101-2026-07-17.png`

## 未完項目與後續順序

1. **真實醫療 reviewer/publish gate**：真實 identity/RBAC、approve/reject、發布責任、disclosure；不可以 demo actor 取代。
2. **AF 121-slot 最終 gate**：處理 20 stale / 93 empty，但只能在 reviewed scope、權威證據與 source-use gate 同時通過時 bounded 生成。
3. **RAG 證據完整性**：多機構/多類型 sources、conflict detector、included/excluded 理由、withdrawal/supersession refresh。
4. **Review/Hierarchy Admin 完整化**：claim/content/citation diff、逐 claim edit，hierarchy stale preview、DOM＋screenshot visual diff。
5. **完整產品 UX**：feedback queue、offline/RAG outage/retry/cancel/progress、真實全域 modules、localization、WCAG/screen-reader。
6. **安全/維運**：secret manager、token rotation/scope/revocation、CSRF/RBAC、PHI screenshot block、SBOM/vulnerability、structured metrics/cost。
7. **Runtime 擴展**：durable queue/lease/heartbeat/DLQ/idempotency/circuit breaker，再評估 PostgreSQL。
8. **Multi-disease golden set**：內科、急症、腫瘤、感染、兒/婦、大量 guideline 與 insufficient-evidence 疾病。
9. **VM/production delivery**：topology/connectivity/TLS/secrets/backup path、VM browser E2E、rollback/restore、monitoring、RPO/RTO。

## 已知限制（不得隱藏）

- 只有 9 個 AF slots 是 current ready；不是 121-slot 全頁完成。
- 8 個 demo slots 仍為 clinical review pending，新增 proof slot 仍是 generated；沒有真實醫師批准，也沒有 published content。
- Search 是自有 topic/heading search，不是內文全文搜尋。
- 許多 specialty detail tree 仍空；當前以 Featured AF + search 作真實入口。
- 未完全域模組為 disabled，不是已建置。
- 未做正式 WCAG、醫療人因、load/concurrency、security audit、VM DR。
- `docker-compose.yml` 仍有 top-level `version` obsolete 非阻塞警告。
- Worktree 原本就非乾淨；後續 agent 不可轉回或覆蓋其他人的修改。

## 下一位 agent 開工 gate

1. 先讀 Master Checklist，不以本 handoff 取代唯一完成定義。
2. 重查 live health、121/8/20/93 與 manifest hash；數值漂移先停工找根因。
3. 一次只開一個 bounded 施工包，red→green 後再做累積回歸。
4. 所有新 code file 繼續使用 7 欄 header comments，非直觀簡化加 `ponytail:` ceiling/upgrade note。
5. 不重掃 DynaMed 作 runtime、不批量重生、不假造 reviewer、不把 stale/empty 宣稱為完成。
