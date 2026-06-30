<!--
  檔案路徑: rootmedicals-a/doc/LLMXX_IMPLEMENTATION_HANDOFF_2026-06-15.md
  產生時間: 2026-06-17 16:10 +08:00
  版本: v0.1-交付整理
  說明: 交付文件與規劃/驗證紀錄。
  交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
  ----------------------------------------------------------------------------------------------------
-->

<!--
File Path: rootmedicals-a/doc/LLMXX_IMPLEMENTATION_HANDOFF_2026-06-15.md
Timestamp: 2026-06-15 21:52 +08:00
Version: v0.1
Description:
  Handoff for the next AI or engineer continuing llmxx-server Phase 1/2,
  demo/latest, replay, and LAVA Phase 3/4 work.
Change Notes:
  - v0.1: Added startup, endpoint, AES/HTTPS, validation, test, demo, and
    residual-risk instructions.
Safety Notes:
  - No API keys, AES keys, raw OCR images, screenshots, raw diagnostic payloads,
    or private patient identifiers are recorded here.
Verification Notes:
  - Based on py_compile, node --check, live API replay, smoke tests, background
    RAG completion, and 15-loop verification on 2026-06-15.
----------------------------------------------------------------------------------------------------
-->

# LLMXX Implementation Handoff

## Current Status

Implemented and verified:

- `llmxx-server` Phase 1 intake server
- Phase 2 RAG `/api/v1/rag/check` adapter
- Phase 3 optional LAVA `clinical_soap_parse`
- Phase 4 optional LAVA `llmaaj_adjudicate`
- `/demo/latest` minimal clinical review viewer
- PowerShell replay script
- HTTP smoke test script
- 15-loop non-regression verification
- Source inventory and implementation notes

Normal demo server during this pass:

- `http://127.0.0.1:8017`

Loop test server during this pass:

- `http://127.0.0.1:8018`

## Start llmxx-server

```powershell
cd "C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-server"
python -m uvicorn server_app.main:app --host 127.0.0.1 --port 8017
```

Useful environment variables:

```powershell
$env:LLMXX_RAG_CHECK_URL = "http://127.0.0.1:33301/api/v1/rag/check"
$env:LLMXX_LAVA_TASK_BASE_URL = "http://127.0.0.1:33301/api/lava/tasks"
$env:LLMXX_RAG_TIMEOUT_SECONDS = "8"
$env:LLMXX_RAG_BACKGROUND_TIMEOUT_SECONDS = "35"
$env:LLMXX_LAVA_TIMEOUT_SECONDS = "2"
```

## Replay A Formal Client Payload

```powershell
cd "C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-server"
.\Demo-Replay-Payload.ps1 -EndpointUrl "http://127.0.0.1:8017/api/intake" -NewSessionId
```

Default replay source:

- `llmxx-client-local-ocr/server_payloads/YYYYMMDD/server_payload_*.json`
- The script prefers newest filename timestamp with usable SOAP A/P fields.

Use `-PayloadPath` to force a specific sample.

## Connect The Real Client

Client config:

- `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-client-local-ocr\config\default_config.json`

Set:

```json
{
  "network": {
    "endpoint_url": "http://127.0.0.1:8017/api/intake",
    "send_enabled": true,
    "allow_insecure_localhost_http": true
  }
}
```

For production-like non-localhost transport, use HTTPS. The existing client already rejects insecure non-localhost HTTP unless explicitly allowed.

## Formal JSON Contract

Accepted schema:

```json
{
  "schema_version": "llmxx-client-local-ocr.v0.1",
  "session_id": "llmxx-example",
  "created_at": "2026-06-15T00:00:00Z",
  "source": "llmxx-client-local-ocr",
  "input_origin": "clinical_guard_standalone",
  "zero_disk_image_io": true,
  "patient_uid": "[REDACTED]",
  "soap": {
    "S": "",
    "O": "",
    "A": "",
    "P": ""
  },
  "vital_signs": {
    "bp": "",
    "hr": "",
    "temp": "",
    "rr": "",
    "spo2": ""
  },
  "clinical_text": "",
  "redactions": {},
  "normalizations": {}
}
```

Rejected:

- Diagnostics wrappers
- Raw OCR text
- Screenshots
- Image/base64 fields
- Unknown schema versions
- Same `session_id` with a different payload hash

## AES Envelope

Accepted encrypted envelope:

```json
{
  "schema_version": "llmxx-client-local-ocr.encrypted.v0.1",
  "algorithm": "AES-256-GCM",
  "nonce_b64": "...",
  "ciphertext_b64": "..."
}
```

Server env var:

```powershell
$env:LLMXX_SERVER_AES256_KEY_B64 = "<32-byte-key-base64>"
```

Notes:

- Encrypted envelopes are validated before generic base64/image rejection.
- Decrypted plaintext must still be the formal v0.1 client payload.
- `cryptography` is listed in `llmxx-server/requirements.txt`.

## RAG Behavior

Foreground `/api/intake` behavior:

- Calls RAG with `LLMXX_RAG_TIMEOUT_SECONDS`.
- If RAG finishes, returns completed/not_evaluable response according to final gate.
- If RAG times out, returns 202 degraded with `error_code=rag_timeout`.
- On foreground timeout, schedules background RAG completion with `LLMXX_RAG_BACKGROUND_TIMEOUT_SECONDS`.
- `/demo/latest` can update later from degraded to completed/not_evaluable.

Safety:

- llmxx-server never upgrades RAG traffic-light safety.
- Missing sources, failed claim/demo verifier, or contraindication/safety warnings remain review/not_evaluable.

## LAVA Phase 3/4

New optional tasks:

- `clinical_soap_parse`
- `llmaaj_adjudicate`

Files:

- `rootmedicals-a/ebm-rag/lava/matching_tasks/clinical_soap_parse.py`
- `rootmedicals-a/ebm-rag/lava/matching_tasks/llmaaj_adjudicate.py`
- `rootmedicals-a/ebm-rag/lava/task_registry.py`
- `rootmedicals-a/ebm-rag/lava/api_router.py`

Important:

- The running `http://127.0.0.1:33301` RAG/LAVA process was already active during this pass and still showed the old task list until restart.
- Restart RAG/LAVA to load the new task registry before expecting the UI to show the two new task bindings.
- Both new tasks are `required=False`; they should not block existing RAG readiness.

## Demo

Open:

```text
http://127.0.0.1:8017/demo/latest
```

Controls:

- `Refresh` -> native link to `/demo/latest`
- `Open JSON` -> native link to `/api/demo/latest`
- `Open Events` -> JS-updated native link to `/api/sessions/{session_id}/events`

The Browser automation tool could inspect the UI and confirm link hrefs, but its click layer did not navigate even when hit-testing proved the pointer was over the correct anchor. Direct endpoint navigation/API checks passed. Human/manual browser click should be included in the next live demo checklist.

## Verification Already Run

Passed:

- Python syntax:
  - all touched llmxx-server Python files
  - LAVA registry/router
  - new LAVA matching task files
- JS syntax:
  - `static/js/app.js` via bundled Node
- API:
  - `GET /api/health`
  - replay formal payload
  - smoke contract tests
- Background behavior:
  - foreground 202 within client timeout
  - background RAG completion updates events/session
- 15 loops:
  - `rootmedicals-a/llmxx-server/data/verification_loops/loop_summary.json`

Primary docs:

- `rootmedicals-a/doc/LLMXX_IMPLEMENTATION_SOURCE_INVENTORY_2026-06-15.md`
- `rootmedicals-a/doc/LLMXX_15_ITERATION_VERIFICATION_2026-06-15.md`
- `rootmedicals-a/doc/LLMXX_LAVA_RAG_MODIFICATION_PLAN_2026-06-15.md`

## Residual Risks

1. Current indexed RAG literature is AF-focused; client samples are mostly non-AF, so RAG may correctly return review/not_evaluable for demo samples.
2. LAVA task UI needs a RAG/LAVA service restart to show the new optional tasks.
3. Browser automation click limitation prevented full automated click navigation proof, but DOM hrefs and API endpoints were verified.
4. Real OCR tray capture was not re-run in this pass; use `C:\Users\Franky Kuo\Desktop\rootmedicals\rootmedicals-a\llmxx-client-local-ocr\Start-LocalOCR-System.ps1` for the next physical client-to-server demo.
5. Production HTTPS/AES key provisioning still needs environment-specific setup.
