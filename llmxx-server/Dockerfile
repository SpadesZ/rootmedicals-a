# 檔案路徑: rootmedicals-a/llmxx-server/Dockerfile
# 產生時間: 2026-06-18 12:22 +08:00
# 版本: v0.3
# 最後更新: 2026-09-03（首次實際用於 GCP 正式部署，補齊系統函式庫並釘 CPU-only torch）
# 說明:
#   llmxx-server container image。外層資料夾是服務專案，Python app package 位於 server_app。
# 維護提醒:
#   v0.2 以前這份只用於 CI smoke test，未經正式部署驗證，因此下列兩個問題長期未被發現。
# ----------------------------------------------------------------------------------------------------

FROM python:3.10-slim
WORKDIR /app

# easyocr / opencv 需要的系統函式庫。python:3.10-slim 一個都沒有。
# 少了 libxcb.so.1，`import easyocr` 會拋 ImportError，伺服器端 OCR 全面失效：
# /api/intake 回 400「easyocr is required for server-side OCR」。
# 難查之處在於 /api/health 仍回報 "ocr":"ready" —— 該欄位不做實際 import 測試。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 先釘 CPU-only torch。easyocr 依賴 torch，讓它自行解析會從預設 index 拉進
# CUDA 版（實測 2.14.0+cu130）連同 nvidia-cublas/cudnn/nccl 整串套件，
# 在沒有 GPU 的 VM 上白吃數 GB 空間與下載時間。
# 2.6.0 是 PyTorch CPU index 上最後一個提供 cp310 wheel 的版本；
# --extra-index-url 不可省，否則抓不到只存在於 PyPI 的建置依賴。
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        torch==2.6.0+cpu torchvision==0.21.0+cpu

RUN pip install --no-cache-dir -r requirements.txt

# 建置期就驗證 easyocr 真的 import 得動，讓缺函式庫在 build 階段直接失敗，
# 而不是等到執行期才由 /api/intake 回 400。
RUN python -c "import easyocr, cv2, torch; print('easyocr/cv2/torch OK', torch.__version__)"

COPY . .
CMD ["uvicorn", "server_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
