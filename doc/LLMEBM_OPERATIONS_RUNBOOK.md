<!--
模組定位: llmebm 本機與 VM 的 stop-backup-migrate-verify-restore 維運手冊。
主要責任: 固定 SQLite 資料安全步驟、失敗停止條件與可稽核 receipt。
呼叫來源: deployment、schema migration、rollback、DR drill 與正式交付驗收。
輸入契約: 已確認的資料庫路徑、備份目錄、維護時段與操作者身份。
輸出契約: integrity=ok 的備份/還原 JSON receipt；服務健康與關鍵資料抽查通過。
安全邊界: 不將資料庫、receipt、secret 或 PHI 上傳外部服務；VM 加密與 retention 另行核准。
維護提醒: VM DNS/ports/backup path 尚未確認前，不得把本機演練宣稱為 VM DR PASS。
-->

# llmebm Operations Runbook

## 適用資料庫

llmebm 至少包含 `sidebar_menu.db`、`topic_content.db`、`ebm_knowledge.db`、`specialty.db`。實際路徑以部署環境變數與 volume mapping 為準，不能依猜測操作。

## Secret preflight

compose 會從未追蹤的根目錄 `.env.topic` 載入三個彼此獨立的 secret：

- `LLMEBM_TOPIC_GENERATION_TOKEN`
- `LLMEBM_REVIEW_ADMIN_TOKEN`
- `LLMEBM_HIERARCHY_ADMIN_TOKEN`

只可由 `.env.topic.example` 複製 key，再以部署環境的 secret generator／manager
填入長隨機值。不得將值寫入 tracked profile、URL、log、browser storage 或驗收報告。
容器重建後若任一受保護 API 回報 token 未配置，視為 fail-closed，該管理流程不得宣稱可用。

## Buyer demo quick start / restart

以 `rootmedicals-a` 為當前目錄，不重跑生成：

```powershell
docker compose -f llmebm/docker-compose.yml up -d llmebm-standalone
Invoke-RestMethod http://localhost:33300/api/health
Start-Process http://localhost:33300/topic/atrial-fibrillation
```

單純重啟使用 `docker restart rootmedicals_llmebm_ui`，之後必須重驗
`/api/health`、AF manifest 與 content status。不可把「容器已 running」當成內容完整。

2026-07-17 buyer-demo 可稽核 baseline 是 121 slots，其中 8 ready / 20 stale /
93 empty；8 個 demo slots 皆為 `review_pending`，不是醫療已批准或已發布。
AF `dom_hash` 必須為
`sha256:efd5dabd7d653f36c667f2555d814f6f9343820112d45b25e5d17039a9221ed6`。
任一數值不合就停止 demo，不可現場觸發全頁生成來掩蓋問題。

## Buyer demo snapshot / reset

建立可回復快照時可使用 SQLite 原生 backup API，不必停止讀取型 demo：

```powershell
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
python -m llmebm.tools.sqlite_backup backup `
  --source llmebm/data/system/topic_content.db `
  --output "runtime_reports/llmebm-demo-topic-content-$stamp.db"
```

reset 前先將備份還原到隔離檔案驗證，不覆寫 live DB：

```powershell
python -m llmebm.tools.sqlite_backup restore `
  --backup <verified-backup.db> `
  --target <isolated-restore.db> `
  --expected-sha256 <sha256:...>
```

只有隔離還原的 `integrity=ok` 且 SHA 一致時，才可停 writer 執行 live reset：

```powershell
docker stop rootmedicals_llmebm_ui
python -m llmebm.tools.sqlite_backup restore `
  --backup <verified-backup.db> `
  --target llmebm/data/system/topic_content.db `
  --expected-sha256 <sha256:...> `
  --replace
docker start rootmedicals_llmebm_ui
```

restore 會先留下 `topic_content.pre-restore-<UTC>.db`。若 restore 或後續驗收失敗，
先確保容器已啟動，但不得開放 demo；保留 stdout JSON receipt、當前 DB 與
pre-restore backup，交由維運人員比對，不可刪除或再生成內容。

## Stop → backup → migrate → verify

1. 開維護時段，停止 llmebm writer/worker，確認沒有 running generation job。
2. 對每個 DB 執行：

   ```powershell
   python llmebm/tools/sqlite_backup.py backup --source <db> --output <timestamped-backup.db>
   ```

3. 保存 stdout JSON receipt；必須包含 `integrity=ok`、SHA-256 與 bytes。
4. 執行 migration，再以 `verify --database <db>` 驗完整性。
5. 啟動服務，驗 `/api/health`、三個 secret key 已配置但不輸出值、AF manifest slot count/hash、content status、admin mutation fail-closed 與瀏覽器 critical flow。
6. 任一 gate 失敗即停止發布，不得繼續生成或人工修改 live DB。

## Restore／rollback

1. 停止所有 SQLite writer。
2. 先用 `verify` 驗證備份並取得 receipt 中的 SHA-256。
3. 明確指定 digest 與覆寫：

   ```powershell
   python llmebm/tools/sqlite_backup.py restore --backup <backup.db> --target <live.db> --expected-sha256 <sha256:...> --replace
   ```

4. 工具會先建立 `*.pre-restore-<UTC>.db` safety backup；restore 後再跑 integrity check。
5. 啟動服務並重跑 health、DB→DOM→manifest、review/publish/source-gate 與 browser smoke。

## 尚未解除的正式 gate

- VM backup path、加密方式、retention、access control、RPO/RTO 尚需部署方確認。
- 本工具提供單機 SQLite 一致快照；不等同 multi-instance durable queue 或 PostgreSQL HA。
- 第一次 VM restore 必須在隔離環境演練，不得直接拿 production 當測試場。
