<!--
  檔案路徑: rootmedicals-a/doc/ROOTMEDICALS_A_DELIVERABLE_HANDOFF_2026-06-15.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: 交付文件與規劃/驗證紀錄。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

# File Path: rootmedicals-a/doc/ROOTMEDICALS_A_DELIVERABLE_HANDOFF_2026-06-15.md
# Timestamp: 2026-06-17 15:05 +08:00
# Version: v0.2
# Description:
#   Final handoff for the rootmedicals-a deliverable boundary after importing
#   local OCR materials, tightening runtime paths, validating client-to-server
#   flow, and running the final 15-loop replay check.
# Safety Notes:
#   This document does not include API keys, AES keys, raw screenshots, raw OCR
#   images, or patient-identifying data.
# Verification Notes:
#   Evidence is from local commands and browser/API checks run on 2026-06-15.
# Change Notes:
#   - v0.2: Consolidate public launch through RootMedicals-Control and update
#     client paths after llmxx-client/apps consolidation.
# ----------------------------------------------------------------------------------------------------

# RootMedicals-A Deliverable Handoff

## Boundary Decision

The final deliverable is:

```text
C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a
```

The parent folder is only a source/material library. Runtime, demo, replay,
OCR payloads, server tests, and handoff docs must work from inside
`rootmedicals-a` without requiring sibling folders.

## Imported Material Now Inside Rootmedicals-A

- `rootmedicals-a\llmxx-client\apps\local-ocr`
- `rootmedicals-a\llmxx-client\apps\clinicalguard-standalone`

The imported LocalOCR client now posts to:

```text
http://127.0.0.1:8017/api/intake
```

The formal payload output is:

```text
rootmedicals-a\llmxx-client\apps\local-ocr\server_payloads\YYYYMMDD\server_payload_*.json
```

Diagnostics remain separate:

```text
rootmedicals-a\llmxx-client\apps\local-ocr\diagnostics\YYYYMMDD\diagnostics_*.json
```

## Main Launch Command

Use the root control panel for normal operation:

```powershell
cd "<rootmedicals-a>"
.\RootMedicals-Control.cmd
```

The control panel starts/stops:

- `llmxx-server`
- ClinicalGuard mock HIS plus LocalOCR tray
- Live RAG / Live + Synthetic / Demo Fixture modes
- RAG/LAVA Docker stack

Module-level PowerShell scripts remain available as internal engineering
helpers, but public double-click CMD wrappers were moved to
`legacy\launchers`.

## Key Changes Made

### Server Boundary

- `llmxx-server\Demo-Replay-Payload.ps1`
  - Default endpoint is now `http://127.0.0.1:8017/api/intake`.
  - Default payload discovery stays inside `rootmedicals-a\llmxx-client\apps\local-ocr\server_payloads`.

- `llmxx-server\scripts\run_smoke_tests.py`
  - Default server URL is now `http://127.0.0.1:8017`.
  - Default payload root is inside `rootmedicals-a`.
  - After defensive reject/degraded cases, the test restores `/demo/latest` with a valid formal payload so Demo Fixture demos do not open on a negative smoke case.

- `llmxx-server\server_app\infra\settings.py`
  - Removed unused parent-workspace path constant.

### LocalOCR Client Boundary

- `llmxx-client\apps\local-ocr\config\default_config.json`
  - `network.endpoint_url` points to local server port `8017`.
  - `network.send_enabled` is `true`.
  - Added conservative OCR literal correction `fof -> for` from live validation.

- `llmxx-client\apps\local-ocr\Start-LocalOCR-System.ps1`
  - Resolves runtime inside `rootmedicals-a`.
  - Skips broken virtual environments.
  - Waits for the visible ClinicalGuard window before reporting ready.
  - Restricts already-running process detection to this exact ProjectRoot.

- `llmxx-client\apps\local-ocr\Stop-LocalOCR-System.ps1`
  - Restricts cleanup to command lines containing this exact ProjectRoot.
  - Stops current rootmedicals-a HIS/tray processes without touching sibling source folders.

- `legacy\launchers\local-ocr\*.cmd`
  - Historical double-click wrappers retained for traceability only.

### OCR Region Reliability

- `llmxx-client\apps\local-ocr\src\llmxx_client_local_ocr\capture.py`
  - Added Win32 child-control rectangle detection for bundled Tk/ClinicalGuard fields.
  - SOAP regions now align with real Text controls.
  - Vital Signs regions now align with real Entry controls.
  - Existing image-layout and configured fallback routes remain.

- `llmxx-client\apps\local-ocr\src\llmxx_client_local_ocr\layout_detector.py`
  - Made LayoutParser optional.
  - OpenCV candidate scoring can run without LayoutParser.

### Demo Viewer

- `llmxx-server\static\demo_latest.html`
  - Action controls are native GET forms.

- `llmxx-server\static\js\app.js`
  - JavaScript only updates the session-specific Events form target.

- `llmxx-server\static\css\style.css`
  - Form wrappers remain aligned in the compact toolbar.

## Verification Completed

### Static Checks

- PowerShell parser passed for:
  - `llmxx-client\apps\local-ocr\Start-LocalOCR-System.ps1`
  - `llmxx-client\apps\local-ocr\Stop-LocalOCR-System.ps1`
  - `llmxx-client\apps\local-ocr\Setup-LocalOCR-Environment.ps1`
  - `llmxx-server\Demo-Replay-Payload.ps1`
  - `llmxx-server\Start-LLMXX-Server.ps1`
  - `llmxx-server\Stop-LLMXX-Server.ps1`

- Python compile passed for touched LocalOCR and server files.
- Bundled Node `node --check` passed for `llmxx-server\static\js\app.js`.
- Boundary scan found no active formal references to:
  - sibling `llmxx-client-local-ocr`
  - sibling `ClinicalGuard_Standalone`
  - old D-drive rootmedicals-a path
  - removed parent-workspace runtime constants
  - old port 8000 intake default
  - `send_enabled: false`

### Actual LocalOCR Operation

Live GUI flow was run inside `rootmedicals-a`:

1. Started the stack from `RootMedicals-Control.cmd`.
2. Verified `Clinical Guard (Phase 1 Local)` visible.
3. Filled mock HIS through actual Windows click/paste operations.
4. Ran LocalOCR `run-once --send --expected`.
5. Verified formal payload was written under internal `server_payloads`.
6. Verified server accepted the payload.

Final clean OCR result:

```json
{
  "S": "headache for 3 days",
  "O": "alert and oriented",
  "A": "migraine suspected",
  "P": "acetaminophen"
}
```

Verification scores:

```json
{
  "S": 1.0,
  "O": 1.0,
  "A": 1.0,
  "P": 1.0
}
```

### Server Replay And Smoke

- `Demo-Replay-Payload.ps1 -NewSessionId -TimeoutSeconds 20`
  - Default endpoint used `8017`.
  - Payload path was internal to `rootmedicals-a`.
  - Response `ok=true`.

- `scripts\run_smoke_tests.py --timeout 20`
  - Default URL used `http://127.0.0.1:8017`.
  - Smoke response `ok=true`.
  - Defensive reject/degraded cases passed.
  - Demo latest was restored to a valid formal payload at the end.

### Final 15-Loop Replay

Final summary file:

```text
rootmedicals-a\llmxx-server\data\verification_loops_rootmedicals_a_final\loop_summary.json
```

Result:

```json
{
  "total": 15,
  "ok_count": 15,
  "degraded_count": 2,
  "not_evaluable_count": 13,
  "rag_timeout_count": 2
}
```

Interpretation:

- All 15 formal payload replays were accepted by server.
- Two loops hit foreground RAG timeout.
- The system degraded safely to `yellow/review` instead of failing.
- No loop returned an unhandled 5xx or broken JSON response.

## Current Demo State

`/demo/latest` currently shows a valid clinical case:

```json
{
  "dx": "migraine suspected",
  "tx": "acetaminophen",
  "hx": "S: headache for 3 days; O: alert and oriented",
  "light": "yellow",
  "display_mode": "review"
}
```

The `yellow/review` state is expected while RAG/LLM provider calls are
rate-limited or verifier gates are not fully bound. This is a safe downgrade,
not a successful evidence-backed upgrade.

## Known Residual Risks

1. RAG provider can return HTTP 429 or time out.
   - Current behavior is safe: `yellow/review`, `not_evaluable` or `degraded`.
   - It should not be presented as evidence-backed.

2. In-app Browser click bridge did not trigger native form submit during tool testing.
   - DOM forms and endpoint URLs were verified.
   - Shell/API checks confirmed `/api/demo/latest` and session events work.
   - For Demo Fixture demo, use a normal browser window if interactive clicking of JSON/Event controls is important.

3. Not every legacy file in imported material has been normalized to the full new header protocol.
   - Touched files were updated where practical.
   - A future pass should inventory and standardize all legacy headers without changing behavior.

4. Live OCR currently validated against the bundled ClinicalGuard mock HIS.
   - The Win32 child-control detector is strong for Tk/ClinicalGuard.
   - A real hospital HIS adapter may need its own region strategy.

## Recommended Next Improvements

These are safe improvements that should not break or downgrade current flow:

1. Add a deterministic Demo Fixture mode for RAG/EBM output.
   - Purpose: avoid provider 429 during presentations.
   - Keep production behavior unchanged.
   - Use an explicit demo flag or fixture file so it cannot be mistaken for live evidence.

2. Add a dedicated `Restore-Demo-Latest.ps1`.
   - Purpose: set `/demo/latest` to a clean formal payload after any negative tests.
   - Smoke test now does this internally, but a standalone operator script would help demos.

3. Add standard-browser UI automation.
   - Purpose: verify button clicks in real Chrome/Edge rather than the in-app Browser bridge.
   - Keep current API and DOM checks as fallback.

4. Complete a header and notes pass for all legacy/imported code files.
   - Purpose: satisfy the project-wide code note/header standard.
   - Do this as a no-behavior-change documentation pass with parser/compile checks.

5. Extract OCR region detection into named strategies.
   - Purpose: make `win32_child`, `cv_layout`, and `configured_relative` explicit and easier to adapt to a non-Tk HIS.
   - Preserve current priority order for ClinicalGuard.

## Handoff Rule For Next AI

Do not move runtime back to the parent workspace. If a file, script, endpoint,
payload, or demo command requires anything outside `rootmedicals-a`, treat that
as a regression unless the user explicitly changes the deliverable boundary.
