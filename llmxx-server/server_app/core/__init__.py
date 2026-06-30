# 檔案路徑: rootmedicals-a/llmxx-server/server_app/core/__init__.py
# 產生時間: 2026-06-18 11:54 +08:00
# 版本: v0.2
# 模組定位:
#   server core package。臨床 mapping、final gate、Demo Fixture 與 adjudication fallback 都放在此層。
# 維護提醒:
#   - core 可以讀 ClinicalParse/RAG result，但不直接發 HTTP；HTTP adapter 請放 integrations。
#   - green/yellow/orange 的最終規則要集中在 response_builder，避免分散判斷。
# ----------------------------------------------------------------------------------------------------
