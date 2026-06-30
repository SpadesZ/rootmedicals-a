/*
 * 檔案路徑: rootmedicals-a/llmebm/app/static/js/stream_client.js
 * 產生時間: 2026-06-17 16:10 +08:00
 * 版本: v0.1-交付整理
 * 說明: llmebm 知識庫 UI 靜態資源。
 * 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
 * ----------------------------------------------------------------------------------------------------
 */

/*
# 路徑: rootmedicals-a/llmebm/app/static/js/stream_client.js
# 版本: v0.1 
# 說明: 實作 SSE (Server-Sent Events) 客戶端，攔截 FastAPI 推播的串流數據，動態渲染 DOM。
# ----------------------------------------------------------------------------------------------------
*/

document.addEventListener("DOMContentLoaded", () => {
    const btnAnalyze = document.getElementById("btn-analyze-soap");
    const soapInput = document.getElementById("soap-textarea");
    
    // UI 元件參照
    const statusBar = document.getElementById("system-status-bar");
    const statusText = document.getElementById("status-text");
    const resultSection = document.getElementById("ebm-results-section");
    const reasoningText = document.getElementById("llm-reasoning-text");
    const trafficLight = document.getElementById("traffic-light-indicator");
    const dxTxSummary = document.getElementById("result-dx-tx");
    const ocebmLevel = document.getElementById("result-ocebm-level");
    const gradeLabel = document.getElementById("result-grade");
    const referencesList = document.getElementById("rag-references-list");

    if (btnAnalyze) {
        btnAnalyze.addEventListener("click", () => {
            const query = soapInput.value.trim();
            if (!query) {
                alert("Please enter clinical case notes first.");
                return;
            }

            // 1. 初始化介面狀態 (Reset UI)
            btnAnalyze.disabled = true;
            btnAnalyze.style.opacity = "0.6";
            statusBar.style.display = "block";
            resultSection.style.display = "block";
            reasoningText.textContent = "";
            referencesList.innerHTML = "";
            trafficLight.style.backgroundColor = "#ccc";
            trafficLight.style.boxShadow = "none";
            dxTxSummary.textContent = "Synthesizing Guidelines...";
            ocebmLevel.textContent = "--";
            gradeLabel.textContent = "--";

            // 2. 建立 SSE 連線 (呼叫後端 FastAPI)
            const apiUrl = `/api/v1/analyze-soap?query=${encodeURIComponent(query)}`;
            const eventSource = new EventSource(apiUrl);

            // 3. 處理即時數據流
            eventSource.onmessage = function(event) {
                const data = JSON.parse(event.data);

                // 處理進度狀態更新
                if (data.status === "processing" || data.status === "generating") {
                    statusText.textContent = data.message;
                }

                // 處理 LLM 逐字推播 (打字機效果)
                if (data.status === "streaming") {
                    reasoningText.textContent += data.token;
                    // 自動向下滾動到底部以追蹤新文字
                    reasoningText.scrollTop = reasoningText.scrollHeight; 
                }

                // 處理最終完成封包
                if (data.status === "complete") {
                    eventSource.close(); // 關閉串流通道
                    
                    statusBar.style.display = "none"; // 隱藏進度條
                    btnAnalyze.disabled = false;
                    btnAnalyze.style.opacity = "1";

                    // 渲染紅綠燈視覺
                    const lightColor = data.traffic_light.toLowerCase();
                    if (lightColor === "green") {
                        trafficLight.style.backgroundColor = "#28a745";
                        trafficLight.style.boxShadow = "0 0 15px #28a745";
                    } else if (lightColor === "yellow" || lightColor === "caution") {
                        trafficLight.style.backgroundColor = "#ffc107";
                        trafficLight.style.boxShadow = "0 0 15px #ffc107";
                    } else if (lightColor === "red") {
                        trafficLight.style.backgroundColor = "#dc3545";
                        trafficLight.style.boxShadow = "0 0 15px #dc3545";
                    }

                    // 渲染 Metadata
                    dxTxSummary.textContent = data.dx_tx_summary;
                    ocebmLevel.textContent = data.evidence_level;
                    gradeLabel.textContent = data.recommendation_grade;

                    // 渲染文獻列表
                    if (data.references && data.references.length > 0) {
                        data.references.forEach(ref => {
                            const li = document.createElement("li");
                            li.innerHTML = `<strong>${ref.title}</strong> (DOI: <a href="https://doi.org/${ref.doi}" target="_blank" style="color: #0056b3;">${ref.doi}</a>) - Match: ${Math.round(ref.match_score * 100)}%`;
                            referencesList.appendChild(li);
                        });
                    } else {
                        referencesList.innerHTML = "<li>No direct evidence found in the local vector database.</li>";
                    }
                }
            };

            // 處理連線錯誤
            eventSource.onerror = function(err) {
                console.error("SSE Connection Error:", err);
                statusText.textContent = "Connection lost. Please try again.";
                eventSource.close();
                btnAnalyze.disabled = false;
                btnAnalyze.style.opacity = "1";
            };
        });
    }
});