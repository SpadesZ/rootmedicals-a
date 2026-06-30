<!--
  檔案路徑: rootmedicals-a/ebm-rag/docs/EBM_RAG_LAVA_HANDOFF_2026-06-11.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: EBM-RAG 服務入口、Docker 設定、文件或依賴描述。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

<!--
File Path: ebm-rag/docs/EBM_RAG_LAVA_HANDOFF_2026-06-11.md
Timestamp: 2026-06-11 13:57:39 +08:00
Version: v0.2
Description: RootMedicals EBM-RAG and LAVA implementation handoff after 12 improvement rounds.
-->

# RootMedicals EBM-RAG and LAVA Handoff

## Handoff Date

- Date: 2026-06-11
- Scope: `rootmedicals-a/ebm-rag`
- Target runtime: Docker-managed FastAPI app on `33301`
- Current state: demo-capable RAG with LAVA-managed LLM tasks, deterministic retrieval safeguards, optional LLM-assisted query decomposition, and UI-tested Admin/LAVA flows.

## Current Runtime

- App URL: http://localhost:33301
- Admin UI: http://localhost:33301/admin
- LAVA UI: http://localhost:33301/lava
- Docker stack for RAG only: `docker-compose-rag.yml`
- Running containers:
  - `ebm_rag_web_ui` on host port `33301`, container port `8000`
  - `ebm_rag_qdrant` on host ports `6333` and `6334`
- Current health from `GET /api/v1/rag/health`:
  - `ok=true`
  - `status=ready`
  - `lava_ready=true`
  - `indexed_chunk_count=27`
  - active embedding signature: `google / gemini-embedding-001 / dim=3072`
  - quality gate: `query_safe=true`, `passed_chunk_count=27`, `issue_chunk_count=0`
- Important Docker note:
  - Use `docker compose -f docker-compose-rag.yml`.
  - Do not use the parent `rootmedicals-a/docker-compose.yml` for this RAG UI smoke, because that stack can collide with local port `8000`.

## LAVA Task Contract

Only tasks that actually call an LLM API are allowed in LAVA.

1. `semantic_reconstruct`
   - Capability: `chat`
   - Required: `true`
   - Role: Core0 OCR text reconstruction and denoising.
   - Current binding: connection `1`, Gemini chat model.
   - Failure behavior: returns original or raw text with `status=unconfigured` or `status=failed`; must not fabricate OCR output silently.

2. `embedding_dense`
   - Capability: `embedding`
   - Required: `true`
   - Role: Core2 chunk to dense vector conversion.
   - Current binding: connection `2`, Gemini embedding model.
   - Contract: vector dimension must stay consistent inside a Qdrant collection.

3. `ebm_generate`
   - Capability: `chat`
   - Required: `true`
   - Role: Core4 Top-K evidence to EBM result generation.
   - Current binding: connection `1`, Gemini chat model.
   - Contract: output must pass schema validation and source traceability validation before being accepted.

4. `query_decompose`
   - Capability: `chat`
   - Required: `false`
   - Role: optional Core4 query expansion.
   - Current binding: connection `1`, Gemini chat model.
   - Contract: only used when the caller sets `filters.query_decomposition_mode="llm_assisted"`.
   - Fallback: deterministic 4-query retrieval remains the base path and is returned if the optional LLM path fails or degrades.

Non-LLM deterministic tasks must not be added to LAVA task binding.

## RAG Query Modes

### Deterministic Mode

This is the default and must remain the baseline.

- Input:
  - `dx_summary`
  - `case_context.dx`
  - `case_context.tx`
  - retrieval filters
- Query plan:
  - `dx gold standard diagnosis criteria`
  - `tx efficacy for dx`
  - `tx contraindications adverse effects dx`
  - `dx alternative treatments`
- Output evidence:
  - stored under `retrieval.queries`
  - full plan stored under `retrieval.query_plan`
- Expected `query_plan`:
  - `mode="deterministic"`
  - `source="deterministic"`
  - `llm_status="not_requested"`
  - `total_queries=4`

### LLM-assisted Mode

This is optional and must not reduce safety.

- Caller sets:

```json
{"query_decomposition_mode":"llm_assisted"}
```

- Core4 first builds the same deterministic 4 queries.
- Then `query_decompose` may add up to 4 extra validated queries.
- LLM output is treated as untrusted input and filtered by:
  - JSON object parsing
  - loose extraction fallback for near-valid JSON
  - query length bounds
  - duplicate removal
  - dx or tx anchor check
  - maximum query count
  - no JSON-like braces inside individual query strings
- Successful expected `query_plan`:
  - `mode="llm_assisted"`
  - `source="deterministic_plus_llm"`
  - `llm_status="ok"`
  - `total_queries` greater than or equal to 4
- Failure expected `query_plan`:
  - `mode="llm_assisted"`
  - `source="deterministic"`
  - `fallback_reason` explains why the LLM expansion was ignored
  - final retrieval still uses the deterministic 4 queries

### Worst-case Guarantee

The optional LLM path must not be worse than deterministic mode.

- If `query_decompose` is unbound, rate-limited, malformed, empty, off-topic, or times out, retrieval uses deterministic queries.
- If LLM expansion succeeds but downstream generation becomes `not_evaluable`, Core4 automatically reruns deterministic mode and returns that result.
- The returned warnings include `llm_query_decomposition_result_fallback` when this deterministic retry path is used.

## Completed Improvement Rounds

1. Core1 chunk hard cap, Core4 retrieval score, calculator integration, and Admin query summary were aligned.
2. Deterministic chunk quality gate was added and query now fails closed if active indexed chunks are unsafe.
3. Public AHA/ACC AF guideline fixture was created and indexed with required metadata and quality status.
4. LAVA provider adapter errors were made retry-aware, concise, and redacted.
5. Calculator auto-detection was strengthened for CHA2DS2-VASc with history text and negation handling.
6. Contraindication traffic hard gate was fixed so flag-only or keyword-only evidence can force orange.
7. Docker was formalized. `33301` is now served by the compose-managed FastAPI container, not a temporary proxy.
8. Docker health was hardened with Qdrant healthcheck, app dependency gating, and `YOLO_CONFIG_DIR`.
9. Admin UI validation was added for missing paper, missing query, missing retrieval id, and malformed JSON. LAVA verify failure preservation was added.
10. Blocking LAVA alerts were replaced with in-page connection and binding status messages.
11. UI observability was improved for native-text fixtures without Core0 visual nodes and for LAVA preserved verification errors.
12. Optional `query_decompose` was added with strict fallback, `query_plan` observability, Admin Query Tester mode selection, API smoke tests, and browser UI click tests.

## Critical Risk Fixes

- API key exposure:
  - LAVA public connection APIs return masked keys only.
  - `api_key_enc` is removed from external API response payloads.
  - Static scans showed no raw Gemini key in touched files.

- Verify failure regression:
  - Before fix, clicking `Verify Chat` during provider 429 could mark an existing good chat connection inactive.
  - This made `semantic_reconstruct` and `ebm_generate` appear unbound.
  - `lava/api_router.py` preserves an existing active and verified connection when a transient verification failure occurs.
  - Failure response includes `preserved_existing_verification=true`.

- Required versus optional LAVA task readiness:
  - Required tasks still gate readiness.
  - Optional `query_decompose` can be unbound without making RAG not ready.
  - If bound and verified, LAVA UI shows it as ready.

- Query safety:
  - Core4 returns `orange` when retrieved evidence triggers contraindication hard gate.
  - LLM provider failure returns `not_evaluable` only when no safe deterministic fallback can produce a usable result.
  - Optional LLM-assisted query expansion cannot bypass deterministic fallback.

- Generation safety:
  - `ebm_generate` output must pass schema validation.
  - EBM comments with clinical content must cite retrieved chunks.
  - Source fields such as `pmid` and `doi` are cross-checked against retrieved chunks.

## Test Fixtures

- `RM_AF_GUIDE_2023`
  - Source: public AHA/ACC atrial fibrillation guideline PDF.
  - Purpose: high-quality guideline chunks for normal retrieval.
  - Indexed chunks: `24`

- `RM_AF_CONTRA_2023`
  - Source: same public AHA/ACC guideline PDF, page area containing absolute contraindication text.
  - Purpose: traffic light contraindication hard gate.
  - Indexed chunks: `3`

- `RM_76EA6AB0`
  - Older ASR paper fixture.
  - Current state: previous unsafe chunks were quality-blocked, not deleted.

## 2026-06-11 Verification Evidence

### Syntax and Hygiene

Passed:

```powershell
python -m py_compile lava\matching_tasks\query_decompose.py lava\task_registry.py lava\api_router.py rag_core\core4_ragging\retriever.py rag_core\core4_ragging\pipeline.py
```

Passed:

```powershell
& 'C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check app\static\js\admin.js
```

Passed with no findings:

```powershell
$pattern = '[T]ODO|write logic' + ' here|\\.\\.\\.|AIza[0-9A-Za-z_-]{20,}'
rg -n $pattern lava\matching_tasks\query_decompose.py lava\task_registry.py lava\api_router.py rag_core\core4_ragging\retriever.py rag_core\core4_ragging\pipeline.py app\template\admin.html app\static\js\admin.js
```

### Backend and API Smoke

Health:

- `GET http://127.0.0.1:33301/api/v1/rag/health`
- Result:
  - `ok=true`
  - `status=ready`
  - `lava_ready=true`
  - `indexed=27`
  - `query_decompose_ready=true`

LAVA task list:

- `GET http://127.0.0.1:33301/api/lava/tasks`
- Result includes:
  - `semantic_reconstruct`
  - `embedding_dense`
  - `ebm_generate`
  - `query_decompose`

LAVA binding:

- `PUT http://127.0.0.1:33301/api/lava/bindings/query_decompose`
- Body:

```json
{"connection_id":1}
```

- Result:
  - `ok=true`
  - `task_id="query_decompose"`
  - `connection_id=1`

Deterministic query:

- Result:
  - `status="ok"`
  - `light_color="orange"`
  - warning includes `contraindication_hard_gate`
  - `query_plan.mode="deterministic"`
  - `query_plan.source="deterministic"`
  - `total_queries=4`
  - retrieval chunks present

LLM-assisted query:

- Result:
  - `status="ok"`
  - `light_color="orange"`
  - warning includes `contraindication_hard_gate`
  - `query_plan.mode="llm_assisted"`
  - `query_plan.source="deterministic_plus_llm"`
  - `query_plan.llm_status="ok"`
  - `llm_queries=1`
  - `total_queries=5`
  - retrieval chunks present

LAVA invoke for `query_decompose`:

- Endpoint:

```text
POST http://127.0.0.1:33301/api/lava/tasks/query_decompose/invoke
```

- Required body shape:

```json
{
  "connection_id": 1,
  "payload": {
    "dx_summary": "atrial fibrillation anticoagulation with active bleeding risk",
    "case_context": {
      "dx": "atrial fibrillation",
      "tx": "anticoagulation"
    },
    "base_queries": [
      "atrial fibrillation gold standard diagnosis criteria",
      "anticoagulation efficacy for atrial fibrillation",
      "anticoagulation contraindications adverse effects atrial fibrillation",
      "atrial fibrillation alternative treatments"
    ]
  }
}
```

- Result:
  - `ok=true`
  - `status="ok"`
  - `query_count=1`
  - `model="gemini-2.5-flash"`
  - `connection_id=1`

### Browser UI Tests

Admin UI:

- Opened `http://127.0.0.1:33301/admin`.
- Clicked `RAG Operations`.
- Confirmed `query_decomposition_mode` select exists.
- Confirmed default value is `deterministic`.
- Clicked `Refresh` in Readiness.
- UI showed `RAG ready for query`.
- Readiness JSON showed:
  - `ok=true`
  - `status="ready"`
  - `lavaReady=true`
  - `queryDecomposeReady=true`
  - `queryDecomposeRequired=false`

Admin deterministic query:

- Filled:

```text
atrial fibrillation anticoagulation with active bleeding risk
```

- Filled case context:

```json
{"dx":"atrial fibrillation","tx":"anticoagulation","hx":"active bleeding risk","calculators":[]}
```

- Filled filters:

```json
{"prefer_six_s_levels":["System","Summaries","Syntheses"],"min_ocebm":"Level_3"}
```

- Selected `deterministic`.
- Clicked `Run Query`.
- UI result:
  - `resultStatus="ok"`
  - `light="orange"`
  - `mode="deterministic"`
  - `source="deterministic"`
  - `totalQueries=4`
  - warning includes `contraindication_hard_gate`
  - summary displays `query_plan=deterministic`

Admin LLM-assisted query:

- Selected `llm_assisted`.
- Clicked `Run Query`.
- UI result:
  - `resultStatus="ok"`
  - `light="orange"`
  - `mode="llm_assisted"`
  - `source="deterministic_plus_llm"`
  - `llmStatus="ok"`
  - `llmQueries=1`
  - `totalQueries=5`
  - warning includes `contraindication_hard_gate`
  - summary displays `query_plan=llm_assisted`

LAVA UI:

- Opened `http://127.0.0.1:33301/lava`.
- Confirmed task UI contains:
  - `semantic_reconstruct`
  - `embedding_dense`
  - `ebm_generate`
  - `query_decompose`
- Clicked LAVA bindings `Refresh`.
- Confirmed binding values:
  - `semantic_reconstruct` -> `1`
  - `embedding_dense` -> `2`
  - `ebm_generate` -> `1`
  - `query_decompose` -> `1`

## How To Re-run The Core Smoke

1. Start or refresh the RAG stack:

```powershell
Set-Location -LiteralPath 'C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag'
docker compose -f docker-compose-rag.yml up -d --build
```

2. If source files were bind-mounted and only Python or JS changed, a service restart is enough:

```powershell
docker compose -f docker-compose-rag.yml restart ebm-rag-engine
```

3. Confirm health:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:33301/api/v1/rag/health
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:33301/api/lava/readiness
```

4. Confirm LAVA task bindings:

- Open http://localhost:33301/lava.
- Verify:
  - `semantic_reconstruct` to connection `1`
  - `embedding_dense` to connection `2`
  - `ebm_generate` to connection `1`
  - `query_decompose` to connection `1` if LLM-assisted query expansion should be demoed

5. Run Admin deterministic query:

- Open http://localhost:33301/admin.
- Go to `RAG Operations`.
- Click `Refresh`.
- Fill query:

```text
atrial fibrillation anticoagulation with active bleeding risk
```

- Fill case context:

```json
{"dx":"atrial fibrillation","tx":"anticoagulation","hx":"active bleeding risk","calculators":[]}
```

- Fill filters:

```json
{"prefer_six_s_levels":["System","Summaries","Syntheses"],"min_ocebm":"Level_3"}
```

- Select `deterministic`.
- Click `Run Query`.
- Expected:
  - `status=ok`
  - `light_color=orange`
  - `contraindication_hard_gate`
  - `query_plan=deterministic`
  - `total_queries=4`

6. Run Admin LLM-assisted query:

- Keep the same query, case context, and filters.
- Select `llm_assisted`.
- Click `Run Query`.
- Expected when provider responds:
  - `status=ok`
  - `light_color=orange`
  - `query_plan=llm_assisted`
  - `source=deterministic_plus_llm`
  - `total_queries` greater than or equal to `4`
- Expected when provider fails:
  - final result still uses deterministic retrieval
  - `query_plan.fallback_reason` explains the LLM issue
  - hard gate remains active

## Files Changed In The Latest Round

- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\lava\matching_tasks\query_decompose.py`
- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\lava\task_registry.py`
- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\lava\api_router.py`
- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\rag_core\core4_ragging\retriever.py`
- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\rag_core\core4_ragging\pipeline.py`
- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\app\template\admin.html`
- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\app\static\js\admin.js`
- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\ebm-rag\docs\EBM_RAG_LAVA_HANDOFF_2026-06-11.md`

Earlier rounds also changed Docker, Core1, Core3, Core4, Core5, LAVA adapters, LAVA UI, Admin UI, and fixture data. Do not revert those changes.

## Key Code Entry Points

- LAVA task whitelist:
  - `lava/task_registry.py`
- LAVA readiness, binding, and invoke routes:
  - `lava/api_router.py`
- Optional query decomposition implementation:
  - `lava/matching_tasks/query_decompose.py`
- Core4 query planning and Qdrant retrieval:
  - `rag_core/core4_ragging/retriever.py`
- Core4 generation, validation, fallback retry, traffic light, and logging:
  - `rag_core/core4_ragging/pipeline.py`
- Admin Query Tester UI:
  - `app/template/admin.html`
  - `app/static/js/admin.js`

## Remaining Non-blocking Candidates

1. Add provider-native JSON response mode for Google chat calls used by JSON-only tasks. This should reduce malformed JSON from `query_decompose` and `ebm_generate`.
2. Add a visible Admin Query Tester panel that lists `query_plan.base_queries` and `query_plan.llm_queries` separately.
3. Add retrieval log filters for `query_decomposition_mode`, `query_plan.source`, and `fallback_reason`.
4. Add a repository-local smoke runner for health, LAVA readiness, deterministic query, LLM-assisted query, and retrieval log lookup. Keep real browser checks as the required final UI verification.
5. Add a fixture reset command that recreates `RM_AF_GUIDE_2023` and `RM_AF_CONTRA_2023` from the public PDF without touching user uploads.

## Hard Rules For The Next AI

- Do not add non-LLM deterministic tasks to LAVA.
- Do not return raw API keys or encrypted key fields in admin APIs.
- Do not let provider rate limit or transient failure deactivate an already verified connection.
- Do not allow RAG query if the active index quality gate is unsafe.
- Do not bypass the contraindication hard gate with LLM output.
- Do not recompute embeddings in Core3 when Core2 already persisted vectors.
- Do not delete or purge existing papers unless the user explicitly requests it.
- Do not make `query_decompose` required for readiness.
- Do not let `llm_assisted` produce a final result worse than deterministic mode.
- Do not rely on backend-only tests after UI changes; open the web UI and click through the affected controls.
