<!--
  檔案路徑: rootmedicals-a/doc/LLMXX_15_ITERATION_VERIFICATION_2026-06-15.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: 交付文件與規劃/驗證紀錄。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

<!--
File Path: rootmedicals-a/doc/LLMXX_15_ITERATION_VERIFICATION_2026-06-15.md
Timestamp: 2026-06-15 21:48 +08:00
Version: v0.1
Description:
  Fifteen-loop verification and optimization record for llmxx-server Phase 1/2,
  demo/latest, replay, and optional LAVA Phase 3/4 task integration.
Change Notes:
  - v0.1: Added actual-operation test results, optimization decisions, and
    remaining non-blocking opportunities.
Safety Notes:
  - Documentation only. Does not contain secrets, raw OCR images, screenshots,
    raw payload archives, or API keys.
Verification Notes:
  - Based on loop outputs in llmxx-server/data/verification_loops and manual
    API/replay/browser checks on ports 8017 and 8018.
----------------------------------------------------------------------------------------------------
-->

# 15-Loop Verification And Optimization Record

## Test Surfaces Covered Per Loop

Each loop ran `rootmedicals-a/llmxx-server/scripts/run_smoke_tests.py` against `http://127.0.0.1:8018`.

Covered checks:

- `GET /api/health`
- `POST /api/intake` with newest formal client payload
- Same-payload idempotent replay
- Same `client_session_id` conflict rejection
- `GET /api/demo/latest`
- `GET /api/sessions/{session_id}`
- `GET /api/sessions/{session_id}/events`
- Diagnostics wrapper rejection
- Image/base64 payload rejection
- Unsupported schema rejection
- Missing Dx defensive path

The 8018 test server used short dependency timeouts:

- `LLMXX_RAG_TIMEOUT_SECONDS=2`
- `LLMXX_RAG_BACKGROUND_TIMEOUT_SECONDS=3`
- `LLMXX_LAVA_TIMEOUT_SECONDS=1`

This kept the loop fast while exercising the same degradation contracts used by the normal server.

## Loop Results

| Loop | Result | Seconds | Session |
| --- | --- | ---: | --- |
| 1 | pass | 3.18 | `server-0d9fa3ee52c34bb5` |
| 2 | pass | 3.12 | `server-75f5da8954e34769` |
| 3 | pass | 3.13 | `server-3aaf2e778369444f` |
| 4 | pass | 3.18 | `server-5cc8ab01cafe4fa6` |
| 5 | pass | 3.19 | `server-5e74932662a24e66` |
| 6 | pass | 3.26 | `server-51aa91f53527442c` |
| 7 | pass | 3.13 | `server-da27b5cfd850472b` |
| 8 | pass | 3.22 | `server-0014a9c9190943e9` |
| 9 | pass | 3.10 | `server-46830bc8cf474914` |
| 10 | pass | 3.10 | `server-4f6c46009e024dbc` |
| 11 | pass | 3.17 | `server-4de439360f754bd2` |
| 12 | pass | 3.10 | `server-102032ce1add42d8` |
| 13 | pass | 3.13 | `server-82423790cebf4f00` |
| 14 | pass | 3.16 | `server-22d4014f0cd948e8` |
| 15 | pass | 3.04 | `server-1177ddeaa9a44c97` |

Raw loop outputs:

- `rootmedicals-a/llmxx-server/data/verification_loops/loop_01.json` through `loop_15.json`
- `rootmedicals-a/llmxx-server/data/verification_loops/loop_summary.json`

## Optimizations Applied During The Cycle

| Optimization | Why Safe | Applied |
| --- | --- | --- |
| AES envelope validation order | Prevents encrypted payloads from being misclassified as image/base64 payloads; does not relax decrypted payload checks | Yes |
| Dedicated RAG/LAVA timeouts | Prevents formal client 12s timeout from being exceeded; dependency failures remain degraded/not_evaluable | Yes |
| Background RAG completion | Allows `/demo/latest` to mature after foreground timeout; does not block intake | Yes |
| Idempotent cached HTTP status preservation | Prevents same payload replay from changing 202 to 200 | Yes |
| Replay/smoke filename timestamp selection | Avoids touched old payloads becoming demo defaults | Yes |
| Smoke session detail check | Avoids false failure when another demo session becomes latest concurrently | Yes |
| Native demo links | Keeps Refresh/JSON/Events usable even if JS click handlers or popup policies fail | Yes |
| Asset version query strings | Avoids stale demo JS/CSS in browser cache | Yes |
| Optional LAVA Phase 3/4 tasks | Adds requested LLM lines without making existing RAG readiness stricter | Yes |

## Loop-by-Loop Candidate Review

The same candidate categories were reviewed after each loop:

- Reliability: timeout, idempotency, conflict handling, background completion.
- Safety: diagnostics/image rejection, source validation, no RAG safety upgrade.
- Demo: latest viewer load, JSON/events availability, native navigation.
- Maintainability: headers, file responsibility, smoke output, handoff notes.

After loop 15, no new failing candidate remained. Further changes would move beyond the agreed first batch or require real HIS/OCR window operation and a restarted LAVA/RAG service with the new task registry loaded.

## Remaining Non-Blocking Opportunities

1. Add a server-side async job status endpoint for background RAG completion progress.
2. Add an explicit `/api/demo/session/{id}` viewer for stable Demo Fixture demos that do not depend on latest-session ordering.
3. Restart the RAG/LAVA service so the running 33301 process loads `clinical_soap_parse` and `llmaaj_adjudicate` in the LAVA task UI.
4. Add an AF-specific formal demo payload because the current client samples are mostly non-AF while the indexed RAG literature is AF-focused.
5. Add a manual browser/human click checklist for the desktop Browser tool limitation observed during automated click checks.
