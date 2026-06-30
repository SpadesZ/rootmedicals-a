/*
 * 檔案路徑: rootmedicals-a/ebm-rag/app/static/js/index.js
 * 產生時間: 2026-06-17 16:10 +08:00
 * 版本: v0.1-交付整理
 * 說明: RAG 管理介面前端靜態資源。
 * 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
 * ----------------------------------------------------------------------------------------------------
 */

/*
File Path: ebm-rag/app/static/js/index.js
Timestamp: 2026-06-11
Version: v1.1
Description: Main RAG upload UI script. Wires upload execution, history refresh/minimize, and history-to-admin navigation.
----------------------------------------------------------------------------------------------------
*/

document.addEventListener("DOMContentLoaded", () => {
    
    // ==========================================
    // DOM 元件綁定宣告區
    // 嚴格採取單行單一變數宣告
    // ==========================================
    
    const fileInput = document.getElementById('file-input');
    const btnTrigger = document.getElementById('btn-trigger-file');
    const filenameText = document.getElementById('filename-text');
    const btnExecute = document.getElementById('btn-execute');
    
    const historyContainer = document.getElementById('history-container');
    const btnToggleView = document.getElementById('btn-toggle-view');
    const btnRefresh = document.getElementById('btn-refresh');
    
    const modalOverlay = document.getElementById('confirm-modal');
    const consoleBox = document.getElementById('log-console');
    
    // ==========================================
    // 全域狀態變數宣告區
    // ==========================================
    
    let currentPaperId = null;
    let pollInterval = null;
    let isMinimized = false;
    
    /**
     * 狀態鎖物件
     * 用於紀錄各階段已處理的頁數，防止日誌主控台重複輸出相同進度。
     */
    let lastLogState = {
        rasterized: 0,
        layout: 0,
        recognized: 0
    };
    
    // 用於防止 Recognize 階段的高頻刷屏緩存
    let lastRecogStr = "";

    const escapeHtml = (value) => {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
    };

    // ==========================================
    // 1. 動態日誌系統 (Dynamic Logging System)
    // ==========================================
    
    /**
     * 將訊息輸出至前端動態主控台，並控制文字顏色與自動捲動。
     * @param {string} msg - 欲輸出的日誌訊息內容。
     * @param {boolean} isWarning - 標記此訊息是否為降級警告訊息。
     * @param {boolean} isError - 標記此訊息是否為致命錯誤訊息。
     */
    const log = (msg, isWarning = false, isError = false) => {
        const dateObj = new Date();
        const timeString = dateObj.toLocaleTimeString('zh-TW', { hour12: false });
        
        let colorCode = '#10b981'; // 正常狀態：綠色
        
        if (isError === true) {
            colorCode = '#dc2626'; // 致命錯誤：紅色
        } else if (isWarning === true) {
            colorCode = '#f59e0b'; // 降級/超時警告：橘黃色
        }
        
        const logHtml = `
            <div style="color: ${colorCode}; margin-bottom: 4px;">
                [${timeString}] ${msg}
            </div>
        `;
        
        consoleBox.insertAdjacentHTML('beforeend', logHtml);
        
        // 強制容器自動捲動至最底部
        consoleBox.scrollTop = consoleBox.scrollHeight;
    };

    /**
     * 清空日誌主控台，並重置狀態計數鎖。
     */
    const clearLog = () => {
        consoleBox.innerHTML = '';
        lastLogState.rasterized = 0;
        lastLogState.layout = 0;
        lastLogState.recognized = 0;
        lastRecogStr = "";
    };

    // ==========================================
    // 2. 歷史清單存取 (History Fetching)
    // ==========================================
    
    /**
     * 向後端 API 請求歷史文件清單，並呼叫渲染函式更新畫面。
     */
    const fetchHistory = async () => {
        try {
            const response = await fetch('/api/v1/history');
            const data = await response.json();
            
            historyContainer.innerHTML = '';
            
            if (data.length === 0) {
                const emptyMsg = `
                    <div style="text-align:center; color:#9ca3af; padding:15px; font-weight:bold; font-size: 10px;">
                        No historical records found.
                    </div>
                `;
                historyContainer.innerHTML = emptyMsg;
                return;
            }
            
            data.forEach((item) => {
                renderStrip(item);
            });
            
        } catch (error) { 
            console.error("History Fetch Error Occurred:", error); 
        }
    };

    /**
     * 接收單一筆歷史資料，組裝成高度壓縮的單行狀態列 (Status Strip)。
     * @param {Object} item - 包含處理結果的資料物件。
     */
    const renderStrip = (item) => {
        const dateObj = new Date(item.timestamp * 1000);
        const dateString = dateObj.toLocaleString('zh-TW', { hour12: false });
        const safeFilename = escapeHtml(item.filename);
        const safePaperId = escapeHtml(item.paper_id);
        const safeDuration = escapeHtml(item.duration);
        const safeStatus = escapeHtml(String(item.status || '').toUpperCase());
        
        let statusColor = '';
        if (item.status === 'Success') {
            statusColor = 'var(--rag-green)';
        } else {
            statusColor = 'var(--rag-red)';
        }
        
        const stripHTML = `
            <div class="status-strip">
                <div class="strip-item" style="flex: 2.5; font-weight: 800; color: #1f2937;" title="${safeFilename}">
                    ${safeFilename}
                </div>
                
                <div class="strip-item" style="flex: 1.5; font-family: monospace; color: var(--rag-primary);">
                    ID: ${safePaperId}
                </div>
                
                <div class="strip-item" style="flex: 2; color: #6b7280; font-size: 9px;">
                    ${dateString}
                </div>
                
                <div class="strip-item" style="flex: 1; font-weight: 800; color: #4b5563; text-align: center;">
                    ${safeDuration}
                </div>
                
                <div class="strip-item" style="flex: 1; text-align: right; display: flex; align-items: center; justify-content: flex-end;">
                    <span class="badge-status" style="background: ${statusColor}; margin-right: 8px;">
                        ${safeStatus}
                    </span>
                    
                    <button type="button" class="btn-history-execution" data-paper-id="${safePaperId}" title="Open this paper in Admin Viewer">
                        <svg viewBox="0 0 24 24">
                            <path d="M8,5.14V19.14L19,12.14L8,5.14Z" />
                        </svg>
                    </button>
                </div>
            </div>
        `;
        
        historyContainer.insertAdjacentHTML('beforeend', stripHTML);
    };

    // ==========================================
    // 3. UI 互動與監聽邏輯 (UI Event Listeners)
    // ==========================================
    
    // 綁定「最小化」按鈕事件
    btnToggleView.addEventListener('click', () => {
        if (isMinimized === false) {
            isMinimized = true;
            historyContainer.classList.add('minimized');
            btnToggleView.textContent = 'SHOW ALL';
        } else {
            isMinimized = false;
            historyContainer.classList.remove('minimized');
            btnToggleView.textContent = 'MINIMIZE';
        }
    });

    // 綁定「重新整理」歷史清單按鈕事件
    btnRefresh.addEventListener('click', () => {
        const refreshMsg = `
            <div style="text-align:center; padding:10px; color:#9ca3af; font-weight:bold; font-size: 10px;">
                Refreshing data from server...
            </div>
        `;
        historyContainer.innerHTML = refreshMsg;
        fetchHistory();
    });

    historyContainer.addEventListener('click', (event) => {
        const targetBtn = event.target.closest('.btn-history-execution');
        if (targetBtn === null) {
            return;
        }
        const paperId = targetBtn.getAttribute('data-paper-id');
        if (paperId === null || paperId.trim() === '') {
            alert('System Warning: Unable to locate the selected paper ID.');
            return;
        }
        window.location.href = `/admin?paper_id=${encodeURIComponent(paperId.trim())}`;
    });

    btnTrigger.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            const selectedFile = fileInput.files[0];
            filenameText.textContent = selectedFile.name;
            filenameText.style.color = '#1f2937';
            filenameText.style.fontWeight = 'bold';
        }
    });

    btnExecute.addEventListener('click', () => {
        if (fileInput.files.length === 0) {
            alert("System Warning: Please select a valid Medical Document (PDF) before requesting execution.");
            return;
        }
        
        const selectedFileName = fileInput.files[0].name;
        document.getElementById('modal-filename').textContent = selectedFileName;
        modalOverlay.style.display = 'flex';
    });

    document.getElementById('btn-modal-cancel').addEventListener('click', () => {
        modalOverlay.style.display = 'none';
    });

    // ==========================================
    // 4. 管線啟動與非同步輪詢 (Pipeline & Polling)
    // ==========================================
    
    document.getElementById('btn-modal-confirm').addEventListener('click', async () => {
        modalOverlay.style.display = 'none';
        
        btnExecute.disabled = true; 
        btnExecute.innerHTML = 'PROCESSING...';
        
        clearLog();
        const targetFile = fileInput.files[0];
        log(`System is starting the extraction pipeline for: ${targetFile.name}`);
        
        const formData = new FormData(); 
        formData.append('file', targetFile);
        
        try {
            const response = await fetch('/api/v1/upload', { 
                method: 'POST', 
                body: formData 
            });
            const data = await response.json();
            
            if (data.status === 'success') {
                currentPaperId = data.paper_id;
                log(`Paper ID successfully assigned: ${currentPaperId}`);
                log(`Initializing Rasterize Engine...`);
                
                startPolling();
            }
        } catch (error) {
            // 上傳失敗的致命錯誤
            log(`Upload sequence failed: ${error.message}`, false, true);
            
            btnExecute.disabled = false; 
            btnExecute.innerHTML = `
                EXECUTION 
                <svg viewBox="0 0 24 24">
                    <path d="M8,5.14V19.14L19,12.14L8,5.14Z" />
                </svg>
            `;
        }
    });

    const startPolling = () => {
        const allSteps = document.querySelectorAll('.step');
        allSteps.forEach((stepElement) => {
            stepElement.classList.remove('active');
        });
        
        if (pollInterval !== null) {
            clearInterval(pollInterval);
        }
        
        pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/v1/status/${currentPaperId}`);
                const data = await response.json();
                
                updateSteps(data.steps);
                processDynamicLog(data.details);
                
                // 處理底層傳回的致命中斷錯誤
                if (data.details.error !== null) {
                    clearInterval(pollInterval);
                    log(`[FATAL ERROR] Pipeline sequence crashed: ${data.details.error}`, false, true);
                    
                    btnExecute.disabled = false; 
                    btnExecute.innerHTML = `
                        EXECUTION 
                        <svg viewBox="0 0 24 24">
                            <path d="M8,5.14V19.14L19,12.14L8,5.14Z" />
                        </svg>
                    `;
                    fetchHistory();
                    return;
                }

                // 處理管線全數完成
                if (data.is_complete === true) {
                    clearInterval(pollInterval);
                    log(`[SUCCESS] Multi-modal extraction pipeline finished successfully.`);
                    
                    btnExecute.disabled = false; 
                    btnExecute.innerHTML = `
                        EXECUTION 
                        <svg viewBox="0 0 24 24">
                            <path d="M8,5.14V19.14L19,12.14L8,5.14Z" />
                        </svg>
                    `;
                    
                    fileInput.value = "";
                    filenameText.textContent = "No file chosen";
                    filenameText.style.color = '#6b7280';
                    filenameText.style.fontWeight = 'normal';
                    
                    fetchHistory(); 
                }
            } catch (error) {
                console.error("Polling network error:", error);
            }
        }, 1500); 
    };

    const updateSteps = (steps) => {
        const stepKeys = Object.keys(steps);
        stepKeys.forEach((key) => {
            const stepElement = document.getElementById(`step-${key}`);
            if (stepElement !== null && steps[key] === true) {
                stepElement.classList.add('active');
            }
        });
    };

    /**
     * 解析詳細計數器，轉換為抽象化術語的動態進度日誌。
     * @param {Object} details - 包含處理數量與 Fallback 狀態的數據。
     */
    const processDynamicLog = (details) => {
        const total = details.total_pages;
        
        if (total === 0) {
            return; 
        }

        // ============================
        // 第一階段：Rasterize
        // ============================
        if (details.rasterized_pages > lastLogState.rasterized) {
            lastLogState.rasterized = details.rasterized_pages;
            
            if (details.rasterized_pages < total) {
                log(`Rasterizing document pages: ${details.rasterized_pages} / ${total} pages`);
            } else {
                log(`Rasterize phase completed. ${total} pages successfully processed.`);
                log(`Initializing Layout Engine...`);
            }
        }

        // ============================
        // 第二階段：Layout
        // ============================
        if (details.layout_pages > lastLogState.layout) {
            lastLogState.layout = details.layout_pages;
            
            if (details.layout_pages < total) {
                log(`Analyzing semantic layout boundaries: ${details.layout_pages} / ${total} pages`);
            } else {
                log(`Layout phase completed.`);
                log(`Extracting physical objects... Found ${details.cropped_objects} distinct objects.`);
            }
        }

        // ==========================================
        // 第三階段：Recognize (具備 Timeout Fallback 攔截)
        // ==========================================
        if (details.recognize_progress !== null) {
            let prog = details.recognize_progress;
            
            if (prog.status === 'initializing') {
                let initStr = `Booting up Recognize Engine...`;
                if (initStr !== lastRecogStr) { 
                    log(initStr); 
                    lastRecogStr = initStr; 
                }
            } 
            else if (prog.status === 'processing') {
                // 判斷是否觸發 Fallback 降級機制
                let failStr = "";
                let isWarning = false;
                
                if (prog.failed > 0) {
                    failStr = ` [Fallback Activated: ${prog.failed} timeouts/errors bypassed]`;
                    isWarning = true; // 觸發橘黃色警告文字
                }
                
                let pStr = `Recognizing -> Page ${prog.page}: Processing Object [${prog.processed} / ${prog.total}]${failStr}`;
                
                if (pStr !== lastRecogStr) { 
                    // 將是否帶有警告的布林值傳入 log 函式
                    log(pStr, isWarning, false); 
                    lastRecogStr = pStr; 
                }
            }
        }

        if (details.recognized_pages > lastLogState.recognized) {
            lastLogState.recognized = details.recognized_pages;
            
            if (details.recognized_pages === total) {
                log(`Recognize phase completely finished for all pages.`);
                log(`Activating Reconstruct Engine to generate final payload...`);
            }
        }
    };

    // 初始化載入
    fetchHistory();
});
