#!/bin/bash
# 檔案路徑: rootmedicals-control.sh
# 產生時間: 2026-07-06 +08:00
# 說明: Linux/Debian 系統專用的一鍵啟停與狀態監控腳本。
# -----------------------------------------------------------------------------

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$ROOT_DIR/data"
mkdir -p "$DATA_DIR"

# 定義 PID 檔案路徑
QDRANT_PID="$DATA_DIR/qdrant.pid"
EBM_PID="$DATA_DIR/llmebm.pid"
RAG_PID="$DATA_DIR/ebm_rag.pid"
SERVER_PID="$DATA_DIR/llmxx_server.pid"

# 輔助函式：檢查 PID 是否運行
is_running() {
    local pid_file=$1
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "$pid"
            return 0
        fi
    fi
    return 1
}

# 輔助函式：安全殺除行程
kill_pid_file() {
    local pid_file=$1
    local service_name=$2
    local pid=$(is_running "$pid_file")
    if [ ! -z "$pid" ]; then
        echo "正在停止 $service_name (PID: $pid)..."
        kill "$pid"
        for i in {1..10}; do
            if ! ps -p "$pid" > /dev/null 2>&1; then
                rm -f "$pid_file"
                echo "$service_name 已成功停止。"
                return 0
            fi
            sleep 0.5
        done
        echo "警告：$service_name (PID: $pid) 未能正常停止，強制殺除中..."
        kill -9 "$pid"
        rm -f "$pid_file"
    else
        rm -f "$pid_file"
    fi
}

# 狀態檢查
status() {
    echo "=== RootMedicals 服務狀態報告 ==="
    printf "%-15s %-8s %-8s %-25s\n" "服務名稱" "連接埠" "狀態" "PID / 健康檢測"
    printf "%'-60s\n" ""

    # 1. Qdrant
    local qdrant_p=$(is_running "$QDRANT_PID")
    if [ ! -z "$qdrant_p" ]; then
        if curl -s http://localhost:6333/health >/dev/null; then
            printf "%-15s %-8s %-8s %-25s\n" "Qdrant" "6333" "ONLINE" "PID: $qdrant_p (OK)"
        else
            printf "%-15s %-8s %-8s %-25s\n" "Qdrant" "6333" "DEGRADED" "PID: $qdrant_p (無回應)"
        fi
    else
        printf "%-15s %-8s %-8s %-25s\n" "Qdrant" "6333" "OFFLINE" "-"
    fi

    # 2. llmebm
    local ebm_p=$(is_running "$EBM_PID")
    if [ ! -z "$ebm_p" ]; then
        if curl -s http://localhost:8001/api/health >/dev/null; then
            printf "%-15s %-8s %-8s %-25s\n" "llmebm" "8001" "ONLINE" "PID: $ebm_p (OK)"
        else
            printf "%-15s %-8s %-8s %-25s\n" "llmebm" "8001" "DEGRADED" "PID: $ebm_p (無回應)"
        fi
    else
        printf "%-15s %-8s %-8s %-25s\n" "llmebm" "8001" "OFFLINE" "-"
    fi

    # 3. ebm-rag
    local rag_p=$(is_running "$RAG_PID")
    if [ ! -z "$rag_p" ]; then
        if curl -s http://localhost:33301/api/v1/rag/health >/dev/null; then
            printf "%-15s %-8s %-8s %-25s\n" "ebm-rag" "33301" "ONLINE" "PID: $rag_p (OK)"
        else
            printf "%-15s %-8s %-8s %-25s\n" "ebm-rag" "33301" "DEGRADED" "PID: $rag_p (無回應)"
        fi
    else
        printf "%-15s %-8s %-8s %-25s\n" "ebm-rag" "33301" "OFFLINE" "-"
    fi

    # 4. llmxx-server
    local server_p=$(is_running "$SERVER_PID")
    if [ ! -z "$server_p" ]; then
        local health_check=$(curl -s http://localhost:8017/api/health)
        if [ ! -z "$health_check" ]; then
            local mode_desc="Live"
            if echo "$health_check" | grep -q '"demo_fixture":"enabled"'; then
                mode_desc="DemoFixture"
            elif echo "$health_check" | grep -q '"rag_synthetic_fallback":"enabled"'; then
                mode_desc="LiveSynthetic"
            fi
            printf "%-15s %-8s %-8s %-25s\n" "llmxx-server" "8017" "ONLINE" "PID: $server_p ($mode_desc)"
        else
            printf "%-15s %-8s %-8s %-25s\n" "llmxx-server" "8017" "DEGRADED" "PID: $server_p (無回應)"
        fi
    else
        printf "%-15s %-8s %-8s %-25s\n" "llmxx-server" "8017" "OFFLINE" "-"
    fi
}

# 停止所有服務
stop() {
    echo "=== 開始停止所有 RootMedicals 服務 ==="
    kill_pid_file "$SERVER_PID" "llmxx-server"
    kill_pid_file "$RAG_PID" "ebm-rag"
    kill_pid_file "$EBM_PID" "llmebm"
    kill_pid_file "$QDRANT_PID" "Qdrant"
    echo "所有服務停止程序完成。"
}

# 啟動服務
start() {
    local mode=$1
    if [ -z "$mode" ]; then
        mode="Live"
    fi

    if [ "$mode" != "Live" ] && [ "$mode" != "LiveSynthetic" ] && [ "$mode" != "DemoFixture" ]; then
        echo "錯誤：不支援的啟動模式 '$mode'。可用模式：Live, LiveSynthetic, DemoFixture"
        exit 1
    fi

    echo "=== 開始啟動 RootMedicals 服務 (模式: $mode) ==="

    # 1. 啟動 Qdrant (檢查是否已啟動)
    if [ -z "$(is_running "$QDRANT_PID")" ]; then
        if [ -f "$ROOT_DIR/qdrant" ]; then
            echo "正在啟動 Qdrant..."
            nohup "$ROOT_DIR/qdrant" > "$DATA_DIR/qdrant.log" 2>&1 &
            echo $! > "$QDRANT_PID"
            sleep 1
        else
            echo "錯誤：未在根目錄找到 'qdrant' 執行檔！請先下載 Qdrant 獨立版。"
            exit 1
        fi
    else
        echo "Qdrant 已在運行中。"
    fi

    # 2. 啟動 llmebm
    if [ -z "$(is_running "$EBM_PID")" ]; then
        if [ -d "$ROOT_DIR/llmebm/venv" ]; then
            echo "正在啟動 llmebm..."
            source "$ROOT_DIR/llmebm/venv/bin/activate"
            cd "$ROOT_DIR/llmebm"
            nohup uvicorn main_ebm:app --host 0.0.0.0 --port 8001 > "$DATA_DIR/llmebm.log" 2>&1 &
            echo $! > "$EBM_PID"
            deactivate
            cd "$ROOT_DIR"
        else
            echo "警告：未在 llmebm 目錄下找到虛擬環境 (venv)，跳過啟動。"
        fi
    else
        echo "llmebm 已在運行中。"
    fi

    # 3. 啟動 ebm-rag
    if [ -z "$(is_running "$RAG_PID")" ]; then
        if [ -d "$ROOT_DIR/ebm-rag/venv" ]; then
            echo "正在啟動 ebm-rag..."
            source "$ROOT_DIR/ebm-rag/venv/bin/activate"
            cd "$ROOT_DIR/ebm-rag"
            nohup uvicorn main_rag:app --host 0.0.0.0 --port 33301 > "$DATA_DIR/ebm_rag.log" 2>&1 &
            echo $! > "$RAG_PID"
            deactivate
            cd "$ROOT_DIR"
        else
            echo "警告：未在 ebm-rag 目錄下找到虛擬環境 (venv)，跳過啟動。"
        fi
    else
        echo "ebm-rag 已在運行中。"
    fi

    # 4. 啟動 llmxx-server
    if [ -z "$(is_running "$SERVER_PID")" ]; then
        if [ -d "$ROOT_DIR/llmxx-server/venv" ]; then
            echo "正在啟動 llmxx-server..."
            if ! "$ROOT_DIR/llmxx-server/venv/bin/python" -c 'import fastapi, uvicorn, cryptography, PIL, numpy, cv2, easyocr'; then
                echo "錯誤：llmxx-server/venv 缺少必要的 server/OCR 套件，請先安裝 llmxx-server/requirements.txt。"
                exit 1
            fi
            # 設定模式環境變數
            if [ "$mode" == "DemoFixture" ]; then
                export LLMXX_DEMO_FIXTURE_MODE="true"
                export LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK="false"
            elif [ "$mode" == "LiveSynthetic" ]; then
                export LLMXX_DEMO_FIXTURE_MODE="false"
                export LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK="true"
            else
                export LLMXX_DEMO_FIXTURE_MODE="false"
                export LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK="false"
            fi
            
            cd "$ROOT_DIR/llmxx-server"
            nohup "$ROOT_DIR/llmxx-server/venv/bin/python" -m uvicorn server_app.main:app --host 0.0.0.0 --port 8017 > "$DATA_DIR/llmxx_server.log" 2>&1 &
            echo $! > "$SERVER_PID"
            cd "$ROOT_DIR"
        else
            echo "警告：未在 llmxx-server 目錄下找到虛擬環境 (venv)，跳過啟動。"
        fi
    else
        echo "llmxx-server 已在運行中。"
    fi

    echo "啟動指令已送出，請等候數秒後執行 './rootmedicals-control.sh status' 確認狀態。"
}

# 命令分流
case "$1" in
    start)
        start "$2"
        ;;
    stop)
        stop
        ;;
    status)
        status
        ;;
    *)
        echo "用法: $0 {start|stop|status} [Live|LiveSynthetic|DemoFixture]"
        exit 1
        ;;
esac
