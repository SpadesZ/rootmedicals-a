# LLMXX client local/VM acceptance — 2026-07-14

## Outcome

- Local ClinicalGuard: green, yellow, and orange scenarios passed through the real screenshot client.
- VM delivery package: a clean unzip, first-time client environment setup, HTTPS connection, and all three scenarios passed.
- VM endpoint: `https://35.201.225.188.sslip.io/api/intake` with TLS verification enabled.
- `35.201.225.188` is the in-use static GCP address `rootmedicals-vm-ip` attached to `rootmedicals-a`.

## Root cause and repairs

1. The Windows server launcher accepted an environment with only FastAPI/Uvicorn and selected `llmebm/.venv`, which lacked Pillow, numpy, OpenCV, and EasyOCR.
2. Health did not include OCR readiness, so the incomplete runtime reported `ok=true` until the first screenshot request failed.
3. The VM client control panel and intake config used separate hard-coded endpoints.
4. Nginx and the certificate still used the previous VM address, which forced the client to disable TLS verification.
5. The first endpoint-single-source implementation exposed a Windows PowerShell 5.1 UTF-8 JSON parsing issue; the clean-unzip acceptance caught and fixed it.
6. The capture client called `ShowWindow(..., SW_SHOWNORMAL)` before capture, which restored a maximized HIS to a normal window. It now uses `SW_SHOW`, preserving the existing maximized state.

The final implementation requires the complete server/OCR runtime at startup, reports `checks.ocr`, keeps Windows and Linux server environments isolated, derives the control-panel base URL from the client config, reads that config as UTF-8, and uses a valid certificate for the static VM address.

## Acceptance matrix

| Runtime | Scenario | Server result | Light | Reason/gate | Screenshot |
| --- | --- | --- | --- | --- | --- |
| Local | Green | `completed` | green | `evidence_backed` | [local-green.png](artifacts/llmxx-acceptance-2026-07-14/local-green.png) |
| Local | Yellow | `not_evaluable` | yellow | `icd_missing` | [local-yellow.png](artifacts/llmxx-acceptance-2026-07-14/local-yellow.png) |
| Local | Orange | `completed` | orange | `rag_orange_hard_gate` | [fullscreen-orange-after-demo.png](artifacts/llmxx-acceptance-2026-07-14/fullscreen-orange-after-demo.png) |
| VM package | Green | `completed` | green | `evidence_backed` | [vm-green.png](artifacts/llmxx-acceptance-2026-07-14/vm-green.png) |
| VM package | Yellow | `not_evaluable` | yellow | `icd_missing` | [vm-yellow.png](artifacts/llmxx-acceptance-2026-07-14/vm-yellow.png) |
| VM package | Orange | `completed` | orange | `rag_orange_hard_gate` | [vm-orange.png](artifacts/llmxx-acceptance-2026-07-14/vm-orange.png) |

## Final checks

- Local health before shutdown: `ok=true`, `ocr=ready`, `demo_fixture=enabled`.
- VM health over verified HTTPS: `ok=true`, `ocr=ready`, `demo_fixture=enabled`.
- VM services after acceptance: Qdrant, llmebm, ebm-rag, and llmxx-server all online.
- Clean delivery package: 46 files; no `.venv`, `diagnostics`, `runtime`, `legacy`, or cache content.
- Windows PowerShell 5.1 parsed the packaged UTF-8 config successfully.
- Package control status: server online, Demo Fixture VM, client running during acceptance, RAG ready.
- Static checks: Python compilation, PowerShell parsing, Linux `bash -n`, remote Python compilation, and `git diff --check` passed.
- Maximized-window regression: after both green and contraindication-orange Demo review, Win32 reported `IsZoomed=true`; the captured desktop remained 3456 x 2160. Evidence: [green](artifacts/llmxx-acceptance-2026-07-14/fullscreen-green-after-demo.png), [orange](artifacts/llmxx-acceptance-2026-07-14/fullscreen-orange-after-demo.png).
- Post-sync VM rerun: green=`completed/green`, missing-ICD=`not_evaluable/yellow`, active-bleeding contraindication=`completed/orange` with `rag_orange_hard_gate`.

## Delivery artifact

- File: `C:\Users\Franky Kuo\Desktop\rootmedicals\llmxx-client-VM-20260714.zip`
- Size: 129,364 bytes
- SHA-256: `67CFF4AADB7AAC1A05D775D0BFA904C57A4AEFC6426C2B5993E314040C5AC7A3`
