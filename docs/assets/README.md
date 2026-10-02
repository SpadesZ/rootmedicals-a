# Clinical demo screenshot and sample

`clinical-demo.png` is a real browser capture of the event-log panel at `/demo/latest`
recaptured from the isolated fixture instance in the final batch on 2026-10-02. `demo-payload.json` is a fully synthetic case
that selects the repository's fixed `allergic_rhinitis_intranasal_steroid`
fixture in `llmxx-server/server_app/core/demo_fixtures.py`.

Displayed scores, traffic lights, paper identifiers and citations are fixed
demonstration values. The fixture includes a fictitious DOI and PMID; neither
is a verified publication. This screenshot does not establish live retrieval,
clinical validity or OCR performance. No patient record or credential was used.

Source paths: `llmxx-server/server_app/api/main.py`,
`llmxx-server/server_app/core/demo_fixtures.py`
and the served viewer/event panel. Both optional adapter URLs pointed at local
port 9; synthetic fallback was disabled. The check established fixture
provenance, not a live evidence chain.

The original viewer labels are unchanged. Replay the sample with the README
commands; full screen capture uses the root control panel instead.
