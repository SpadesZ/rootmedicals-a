# 檔案路徑: rootmedicals-a/llmxx-server/Dockerfile
# 產生時間: 2026-06-18 12:22 +08:00
# 版本: v0.2
# 說明:
#   llmxx-server container image。外層資料夾是服務專案，Python app package 位於 server_app。
# 維護提醒:
#   本機 demo 主要走 RootMedicals-Control；Dockerfile 保留給 container 化部署與 CI smoke test。
# ----------------------------------------------------------------------------------------------------

FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "server_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
