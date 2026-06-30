<!--
  檔案路徑: rootmedicals-a/doc/LLMXX_IMPLEMENTATION_SOURCE_INVENTORY_2026-06-15.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: 交付文件與規劃/驗證紀錄。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

<!--
File Path: rootmedicals-a/doc/LLMXX_IMPLEMENTATION_SOURCE_INVENTORY_2026-06-15.md
Timestamp: 2026-06-15 21:44 +08:00
Version: v0.1
Description:
  Source inventory and responsibility notes for the llmxx-client to llmxx-server
  to RAG/LAVA EBM implementation pass.
Change Notes:
  - v0.1: Added relevant old/new source map, ownership notes, and exclusions.
Safety Notes:
  - This is a documentation-only artifact. It records file responsibilities and
    does not contain payload secrets, API keys, screenshots, or raw OCR images.
Verification Notes:
  - Cross-checked with rg --files, codegraph, client HANDOFF.md, and smoke tests.
----------------------------------------------------------------------------------------------------
-->

# LLMXX Implementation Source Inventory

## Boundary

This pass covers the first production-shaped bridge from local OCR client payloads to server-side RAG review:

`rootmedicals-a/llmxx-client-local-ocr -> rootmedicals-a/llmxx-server /api/intake -> rootmedicals-a/ebm-rag /api/v1/rag/check -> optional LAVA clinical_soap_parse / llmaaj_adjudicate -> /demo/latest`

The pass intentionally does not rewrite Core0 ingestion, Qdrant indexing, or the client OCR capture engine.

## New Or Heavily Modified llmxx-server Files

| File | Responsibility | Notes |
| --- | --- | --- |
| `rootmedicals-a/llmxx-server/server_app/api/main.py` | FastAPI app, `/api/health`, `/api/intake`, session APIs, `/api/demo/latest`, `/demo/latest`, foreground/background RAG orchestration | Preserves idempotent HTTP status; schedules background RAG completion after foreground `rag_timeout`. |
| `rootmedicals-a/llmxx-server/server_app/infra/settings.py` | Runtime paths and timeout/env settings | Foreground RAG timeout defaults to 8s; background RAG timeout defaults to 35s; optional LAVA timeout defaults to 2s. |
| `rootmedicals-a/llmxx-server/server_app/contracts/schemas.py` | Strict Pydantic schemas for formal client payloads, AES envelope, clinical parse, public response shape | Uses `extra="forbid"` to reject diagnostics wrappers and schema drift. |
| `rootmedicals-a/llmxx-server/server_app/infra/errors.py` | Stable error-code contract | Dependency failures use 202 where the client should not treat the intake as a server crash. |
| `rootmedicals-a/llmxx-server/server_app/infra/security.py` | Payload-shape rejection, AES-GCM decrypt, payload hash, patient HMAC reference | AES envelope is validated before image-like base64 rejection in `api/main.py`. |
| `rootmedicals-a/llmxx-server/server_app/infra/state_db.py` | SQLite `clinical_sessions` and `clinical_events` | Stores minimized clinical/RAG state only; no raw SOAP payload archive by default. |
| `rootmedicals-a/llmxx-server/server_app/core/clinical_mapper.py` | Deterministic SOAP A/P/S/O to Dx/Tx/Hx/labs mapping | Fallback remains authoritative when optional LAVA parse is unavailable. |
| `rootmedicals-a/llmxx-server/server_app/integrations/rag_client.py` | RAG `/api/v1/rag/check` adapter | Sends only Dx/Tx/Hx/labs/top_k; maps timeout/not-ready/malformed cases to degraded contracts. |
| `rootmedicals-a/llmxx-server/server_app/integrations/lava_client.py` | Optional LAVA task invoke adapter | Short timeout; optional tasks cannot block intake. |
| `rootmedicals-a/llmxx-server/server_app/core/adjudicator.py` | LLMAAJ invoke wrapper and deterministic fallback scores | Scores cannot upgrade RAG safety output. |
| `rootmedicals-a/llmxx-server/server_app/core/response_builder.py` | External response builder, source validation, final gate | Can only preserve or downgrade RAG traffic-light safety. |
| `rootmedicals-a/llmxx-server/Demo-Replay-Payload.ps1` | Replay formal client payloads into `/api/intake` | Defaults to newest usable `server_payload_*.json` by filename timestamp. |
| `rootmedicals-a/llmxx-server/scripts/run_smoke_tests.py` | HTTP smoke/contract test runner | Covers health, intake, cache, conflict, demo, session detail, events, diagnostics/image/schema rejects, missing Dx. |
| `rootmedicals-a/llmxx-server/static/demo_latest.html` | Demo Fixture viewer shell | Uses native links for Refresh/JSON/Events so controls work even if JS init fails. |
| `rootmedicals-a/llmxx-server/static/js/app.js` | Demo data renderer | Fetches minimized `/api/demo/latest` and updates Events href. |
| `rootmedicals-a/llmxx-server/static/css/style.css` | Dense clinical-workstation UI styling | Native action links share button styling. |
| `rootmedicals-a/llmxx-server/requirements.txt` | Server dependencies | Added `cryptography` for AES-GCM envelope support. |

## Modified LAVA Files

| File | Responsibility | Notes |
| --- | --- | --- |
| `rootmedicals-a/ebm-rag/lava/task_registry.py` | LAVA task whitelist and UI binding list | Added optional `clinical_soap_parse` and `llmaaj_adjudicate`; both `required=False`. |
| `rootmedicals-a/ebm-rag/lava/api_router.py` | LAVA FastAPI task invoke router | Registered executors for the two new optional tasks. |
| `rootmedicals-a/ebm-rag/lava/matching_tasks/clinical_soap_parse.py` | Optional SOAP semantic parser | JSON-only prompt and defensive normalization. |
| `rootmedicals-a/ebm-rag/lava/matching_tasks/llmaaj_adjudicate.py` | Optional semantic adjudicator/scorer | Uses only supplied clinical parse, RAG comments, and chunks. |

## Existing RAG/LAVA Files To Preserve

| File | Responsibility | Why It Matters |
| --- | --- | --- |
| `rootmedicals-a/ebm-rag/rag_core/core5_api/router.py` | RAG API routes and readiness gates | `/api/v1/rag/check` is the llmxx-server dependency. Do not bypass readiness. |
| `rootmedicals-a/ebm-rag/rag_core/core5_api/schemas.py` | RAG request schemas | `CheckRequest(dx, tx, hx, age, sex, labs, top_k)` is the current server contract. |
| `rootmedicals-a/ebm-rag/rag_core/core4_ragging/pipeline.py` | RAG retrieval and EBM generation pipeline | Produces native `ebm_hits`; llmxx-server adapts but does not replace it. |
| `rootmedicals-a/ebm-rag/rag_core/core4_ragging/retriever.py` | Vector retrieval phases and chunk return | Source validation depends on `retrieval.chunks`. |
| `rootmedicals-a/ebm-rag/rag_core/core4_ragging/traffic_light.py` | Deterministic safety traffic-light rules | llmxx-server final gate cannot upgrade this result. |
| `rootmedicals-a/ebm-rag/rag_core/core4_ragging/demo_verifier.py` | Demo evidence-backed verification gate | Evidence-backed display stays conservative if verifier is missing or failed. |
| `rootmedicals-a/ebm-rag/lava/matching_tasks/ebm_generate.py` | Evidence-only EBM generation | Must continue requiring source-backed JSON. |
| `rootmedicals-a/ebm-rag/lava/matching_tasks/query_decompose.py` | Optional query expansion | Failure must keep deterministic query fallback. |
| `rootmedicals-a/ebm-rag/lava/matching_tasks/claim_verify.py` | Optional claim-to-evidence support check | Supports final display decision but cannot override deterministic hard gates. |

## Client Files Used As Evidence

| File | Responsibility | Notes |
| --- | --- | --- |
| `rootmedicals-a/llmxx-client-local-ocr/HANDOFF.md` | Client handoff and formal server payload contract | Confirms manual capture trigger and formal JSON output inside the deliverable folder. |
| `rootmedicals-a/llmxx-client-local-ocr/config/default_config.json` | Client endpoint/send/AES settings | Delivery default sends to `http://127.0.0.1:8017/api/intake`. |
| `rootmedicals-a/llmxx-client-local-ocr/Start-LocalOCR-System.ps1` | One-click bundled mock HIS + OCR client startup | Used for future end-to-end client demo. |
| `rootmedicals-a/llmxx-client-local-ocr/Stop-LocalOCR-System.ps1` | One-click client shutdown | Use before changing client config during demos. |
| `rootmedicals-a/llmxx-client-local-ocr/server_payloads/20260614/server_payload_175804_975370.json` | Newest useful formal payload sample | Replay default now resolves this sample from `rootmedicals-a`. |

## Exclusions

- `venv`, `__pycache__`, `.pyc`, SQLite runtime files, logs, diagnostics, screenshots, and payload archives are not source ownership targets.
- Older prototypes and `rootmedicals-faild` are reference only, not authoritative for this pass.
- No raw OCR images, raw screenshots, API keys, AES keys, or raw request headers are recorded in this inventory.
