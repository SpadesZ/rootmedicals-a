# File Path: rootmedicals-a/legacy/llmebm-cad-lava/README.md
# Timestamp: 2026-06-17 15:45 +08:00
# Version: v0.1
# Description:
#   Legacy holder for non-active llmebm CAD/LAVA remnants moved out of the
#   active llmebm application tree.
# Safety Notes:
#   The active medical RAG/LAVA runtime remains under rootmedicals-a/ebm-rag/lava.
# ----------------------------------------------------------------------------------------------------

# Legacy llmebm CAD/LAVA Remnants

This folder contains an old `etl_pipeline.py` remnant that referenced
CAD/document-analysis task names such as `task1_doc_parser`,
`task3_rawvision_parser`, and `task4_allreview_parser`.

Those task files and the old empty `llmebm/app/lava` shell were not present as a
working active workflow, and this code was not used by the current RootMedicals
clinical EBM closed loop.

Do not wire this back into `llmebm` unless a future team intentionally revives
that older CAD/document-analysis workflow.
