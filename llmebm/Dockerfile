# 檔案路徑: rootmedicals-a/llmebm/Dockerfile
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmebm 長期 EBM 知識層、taxonomy UI 或容器設定。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# 路徑: rootmedicals-a/llmebm/Dockerfile
# 版本: v0.2
# 更版時間: 2026-05-05 12:40
# 說明: 更新 entrypoint 至 main_ebm.py，符合 V2 架構，保留舊版指令作為註解。
# ----------------------------------------------------------------------------------------------------
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

# [保留原代碼功能與結構，不可刪除]
# 舊版 CMD ["uvicorn", "ebm_engine:app", "--host", "0.0.0.0", "--port", "8000"]
# 舊版 CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# [新版更新代碼] 將進入點提升至根目錄下的 main_ebm
CMD ["uvicorn", "main_ebm:app", "--host", "0.0.0.0", "--port", "8000"]