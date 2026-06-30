/*
 * 檔案路徑: rootmedicals-a/ebm-rag/app/static/js/admin.js
 * 產生時間: 2026-06-17 16:10 +08:00
 * 版本: v0.1-交付整理
 * 說明: RAG 管理介面前端靜態資源。
 * 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
 * ----------------------------------------------------------------------------------------------------
 */

/*
File Path: ebm-rag/app/static/js/admin.js
Timestamp: 2026-06-15
Version: v2.4
Description: RAG Admin 管理腳本。保留歷史清單與 Extraction Viewer，新增 Literature Search、Knowledge Index、Query Tester、Retrieval Log、demo verifier fallback 串接。
             RAG readiness 未通過時禁用 Query，並以 health panel 呈現 LAVA/Qdrant/index 狀態。
----------------------------------------------------------------------------------------------------
*/

document.addEventListener("DOMContentLoaded", () => {
    
    // ==========================================
    // 基礎 DOM 元件綁定區
    // 嚴格採取單一變數獨立成行宣告
    // ==========================================
    
    const paperSelect = document.getElementById('paper-select');
    const btnLoadDetails = document.getElementById('btn-load-details');
    const canvas = document.getElementById('doc-canvas');
    const tbody = document.getElementById('history-tbody');
    const literatureSearchInput = document.getElementById('literature-search-input');
    const btnLiteratureSearch = document.getElementById('btn-literature-search');
    const literatureSearchStatus = document.getElementById('literature-search-status');
    const literatureSearchResults = document.getElementById('literature-search-results');
    
    const deleteModal = document.getElementById('delete-modal');
    const delIdLabel = document.getElementById('del-id-label');
    const btnDeleteCancel = document.getElementById('btn-delete-cancel');
    const btnConfirmDelete = document.getElementById('btn-confirm-delete');
    
    const navViewerTab = document.getElementById('nav-viewer-tab');
    const navRagTab = document.getElementById('nav-rag-tab');
    const navSystemTab = document.getElementById('nav-system-tab');
    const tabViewer = document.getElementById('tab-viewer');
    const tabRag = document.getElementById('tab-rag');
    const tabSystem = document.getElementById('tab-system');

    const ragPaperSelect = document.getElementById('rag-paper-select');
    const ragIndexMetadata = document.getElementById('rag-index-metadata');
    const btnRagIndex = document.getElementById('btn-rag-index');
    const btnRagLoadChunks = document.getElementById('btn-rag-load-chunks');
    const btnRagQuarantineQuality = document.getElementById('btn-rag-quarantine-quality');
    const ragIndexStatus = document.getElementById('rag-index-status');
    const ragChunksOutput = document.getElementById('rag-chunks-output');
    const queryDxSummary = document.getElementById('query-dx-summary');
    const queryCaseContext = document.getElementById('query-case-context');
    const queryFilters = document.getElementById('query-filters');
    const queryDecompositionMode = document.getElementById('query-decomposition-mode');
    const demoSyntheticFallback = document.getElementById('demo-synthetic-fallback');
    const btnRagQuery = document.getElementById('btn-rag-query');
    const ragQueryStatus = document.getElementById('rag-query-status');
    const queryOutput = document.getElementById('query-output');
    const retrievalQueryId = document.getElementById('retrieval-query-id');
    const btnLoadRetrieval = document.getElementById('btn-load-retrieval');
    const retrievalStatus = document.getElementById('retrieval-status');
    const retrievalOutput = document.getElementById('retrieval-output');
    const btnRagRefreshReadiness = document.getElementById('btn-rag-refresh-readiness');
    const ragReadinessStatus = document.getElementById('rag-readiness-status');
    const ragReadinessSummary = document.getElementById('rag-readiness-summary');
    const ragReadinessOutput = document.getElementById('rag-readiness-output');
    const ragQuerySummary = document.getElementById('rag-query-summary');
    const adminBrandForm = document.getElementById('admin-brand-form');
    const brandNameInput = document.getElementById('brand-name-input');
    const brandLogoInput = document.getElementById('brand-logo-input');
    const brandSaveStatus = document.getElementById('brand-save-status');

    // ==========================================
    // 系統狀態變數宣告區
    // ==========================================
    
    let currentDelId = null;
    let currentPayloadData = null; // 用於快取 lit_collector 引擎回傳的巨型 JSON 資料
    let lastRagQueryId = null;
    let lastRagReadiness = null;
    let initialPaperSelectionApplied = false;
    const initialPaperId = new URLSearchParams(window.location.search).get('paper_id') || '';
    const brandStorageKeys = {
        name: 'rootmedicals.brand.name',
        logo: 'rootmedicals.brand.logoDataUrl'
    };
    const maxBrandLogoBytes = 512 * 1024;

    const escapeHtml = (value) => {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
    };

    const setStatusText = (targetElement, message, isError = false) => {
        if (targetElement === null) {
            return;
        }
        targetElement.textContent = message;
        targetElement.style.color = isError ? '#dc2626' : '#4b5563';
    };

    const renderJson = (targetElement, payload) => {
        if (targetElement === null) {
            return;
        }
        targetElement.textContent = JSON.stringify(payload, null, 2);
    };

    const renderValidationError = (targetElement, code, message) => {
        renderJson(targetElement, {
            ok: false,
            status: 'input_error',
            error: code,
            message: message
        });
    };

    const readLocalStorage = (key) => {
        try {
            return window.localStorage.getItem(key);
        } catch (error) {
            return null;
        }
    };

    const writeLocalStorage = (key, value) => {
        try {
            window.localStorage.setItem(key, value);
            return true;
        } catch (error) {
            return false;
        }
    };

    const setBrandSaveStatus = (message, isError = false) => {
        setStatusText(brandSaveStatus, message, isError);
    };

    const applyHeaderBrand = () => {
        if (window.RootmedicalsHeaderControls && typeof window.RootmedicalsHeaderControls.applyStoredBrand === 'function') {
            window.RootmedicalsHeaderControls.applyStoredBrand();
            return;
        }
        window.dispatchEvent(new CustomEvent('rootmedicals:brand-updated'));
    };

    const readLogoFileAsDataUrl = (file) => {
        return new Promise((resolve, reject) => {
            if (file === null || file === undefined) {
                resolve(null);
                return;
            }
            if (file.size > maxBrandLogoBytes) {
                reject(new Error('Logo file must be 512 KB or smaller for browser-local demo settings.'));
                return;
            }
            if (file.type !== '' && file.type.startsWith('image/') === false) {
                reject(new Error('Logo file must be an image.'));
                return;
            }
            const reader = new FileReader();
            reader.onload = () => {
                resolve(typeof reader.result === 'string' ? reader.result : null);
            };
            reader.onerror = () => {
                reject(new Error('Logo file could not be read.'));
            };
            reader.readAsDataURL(file);
        });
    };

    const parseJsonInput = (element, fallbackValue, label) => {
        if (element === null) {
            return fallbackValue;
        }
        const rawText = element.value.trim();
        if (rawText === '') {
            return fallbackValue;
        }
        try {
            return JSON.parse(rawText);
        } catch (error) {
            throw new Error(`${label} JSON parse failed: ${error.message}`);
        }
    };

    const isPlainObject = (value) => {
        return value !== null && typeof value === 'object' && Array.isArray(value) === false;
    };

    const fetchJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        let payload = null;
        try {
            payload = await response.json();
        } catch (error) {
            payload = {
                error: 'Response is not valid JSON'
            };
        }
        if (response.ok !== true) {
            const rawDetail = payload.detail || payload.error || `HTTP ${response.status}`;
            const detail = typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail);
            const err = new Error(detail);
            err.payload = payload;
            throw err;
        }
        return payload;
    };

    const setButtonDisabled = (buttonElement, disabled) => {
        if (buttonElement === null) {
            return;
        }
        buttonElement.disabled = disabled === true;
    };

    const ensurePaperOption = (selectElement, paperId, labelText) => {
        if (selectElement === null || paperId === '') {
            return;
        }
        const existingOption = Array.from(selectElement.options).find((option) => option.value === paperId);
        if (existingOption !== undefined) {
            return;
        }
        const option = document.createElement('option');
        option.value = paperId;
        option.textContent = labelText || paperId;
        selectElement.appendChild(option);
    };

    const selectPaperAndLoad = (paperId, labelText) => {
        const safePaperId = String(paperId || '').trim();
        if (safePaperId === '') {
            setStatusText(literatureSearchStatus, 'Search result has no paper_id.', true);
            return;
        }
        ensurePaperOption(paperSelect, safePaperId, labelText);
        ensurePaperOption(ragPaperSelect, safePaperId, labelText);
        paperSelect.value = safePaperId;
        if (ragPaperSelect !== null) {
            ragPaperSelect.value = safePaperId;
        }
        btnLoadDetails.click();
    };

    const renderLiteratureResults = (payload) => {
        if (literatureSearchResults === null) {
            return;
        }
        literatureSearchResults.textContent = '';
        const results = Array.isArray(payload.results) ? payload.results : [];
        if (results.length === 0) {
            const emptyState = document.createElement('div');
            emptyState.className = 'literature-result-meta';
            emptyState.textContent = 'No matching literature found.';
            literatureSearchResults.appendChild(emptyState);
            return;
        }
        results.forEach((item) => {
            const paperId = String(item.paper_id || '').trim();
            const labelText = `${item.filename || item.title || paperId} (${paperId})`;
            const resultRow = document.createElement('div');
            resultRow.className = 'literature-result';

            const title = document.createElement('div');
            title.className = 'literature-result-title';
            title.textContent = item.title || item.filename || paperId || 'Untitled literature';

            const loadButton = document.createElement('button');
            loadButton.type = 'button';
            loadButton.className = 'literature-result-button';
            loadButton.textContent = 'Load';
            loadButton.disabled = paperId === '';
            loadButton.addEventListener('click', () => {
                selectPaperAndLoad(paperId, labelText);
            });

            const meta = document.createElement('div');
            meta.className = 'literature-result-meta';
            meta.textContent = [
                paperId || 'paper_id unknown',
                item.doi ? `DOI ${item.doi}` : '',
                item.pmid ? `PMID ${item.pmid}` : '',
                `chunks ${Number(item.indexed_chunk_count || 0)}/${Number(item.chunk_count || 0)}`
            ].filter((part) => part !== '').join(' | ');

            const match = document.createElement('div');
            match.className = 'literature-result-match';
            const matches = Array.isArray(item.matches) ? item.matches : [];
            const matchedChunks = Array.isArray(item.matched_chunks) ? item.matched_chunks : [];
            const chunkText = matchedChunks.length > 0 ? ` | chunks: ${matchedChunks.join(', ')}` : '';
            match.textContent = `matched fields: ${matches.join(', ') || 'metadata'}${chunkText}`;

            resultRow.appendChild(title);
            resultRow.appendChild(loadButton);
            resultRow.appendChild(meta);
            resultRow.appendChild(match);
            literatureSearchResults.appendChild(resultRow);
        });
    };

    const runLiteratureSearch = async () => {
        if (literatureSearchInput === null || btnLiteratureSearch === null) {
            return;
        }
        const query = literatureSearchInput.value.trim();
        if (query.length < 2) {
            setStatusText(literatureSearchStatus, 'Enter at least 2 characters.', true);
            renderLiteratureResults({
                results: []
            });
            return;
        }
        btnLiteratureSearch.disabled = true;
        setStatusText(literatureSearchStatus, 'Searching literature metadata...');
        try {
            const payload = await fetchJson(`/api/v1/rag/literature/search?q=${encodeURIComponent(query)}&limit=12`);
            renderLiteratureResults(payload);
            setStatusText(literatureSearchStatus, `Found ${payload.total || 0} matching literature record(s).`);
        } catch (error) {
            renderLiteratureResults({
                results: []
            });
            setStatusText(literatureSearchStatus, error.message, true);
        } finally {
            btnLiteratureSearch.disabled = false;
        }
    };

    const readinessBadgeClass = (isOk) => {
        return isOk === true ? 'rag-health-ok' : 'rag-health-bad';
    };

    const appendReadinessCard = (label, isOk, valueText) => {
        if (ragReadinessSummary === null) {
            return;
        }
        const card = document.createElement('div');
        card.className = 'rag-health-card';
        const strong = document.createElement('strong');
        strong.textContent = label;
        const badge = document.createElement('span');
        badge.className = readinessBadgeClass(isOk);
        badge.textContent = valueText;
        card.appendChild(strong);
        card.appendChild(badge);
        ragReadinessSummary.appendChild(card);
    };

    const getTaskReady = (readiness, taskId) => {
        const task = readiness?.readiness?.lava?.tasks?.[taskId] || readiness?.lava?.tasks?.[taskId] || {};
        return task.ready === true;
    };

    const getActiveQuality = (readinessPayload) => {
        const readiness = readinessPayload?.readiness || readinessPayload || {};
        return readiness.active_embedding_index?.quality || null;
    };

    const qualityLabel = (quality) => {
        if (quality === null || typeof quality !== 'object') {
            return 'Unknown';
        }
        const issueCount = Number(quality.issue_chunk_count || 0);
        const totalCount = Number(quality.indexed_chunk_count || 0);
        if (quality.query_safe === true) {
            return `Safe ${totalCount}/${totalCount}`;
        }
        return `Blocked ${issueCount}/${totalCount}`;
    };

    const shouldDisableIndex = (readinessPayload) => {
        if (readinessPayload === null) {
            return true;
        }
        const readiness = readinessPayload.readiness || readinessPayload;
        const sqliteOk = readiness.sqlite === true;
        const qdrantOk = readiness.qdrant?.ok === true;
        const embeddingReady = getTaskReady(readinessPayload, 'embedding_dense');
        return sqliteOk !== true || qdrantOk !== true || embeddingReady !== true;
    };

    const shouldDisableQuery = (readinessPayload) => {
        return readinessPayload === null || readinessPayload.ok !== true;
    };

    const renderRagReadiness = (payload) => {
        lastRagReadiness = payload;
        renderJson(ragReadinessOutput, payload);
        if (ragReadinessSummary !== null) {
            ragReadinessSummary.innerHTML = '';
        }

        const readiness = payload.readiness || {};
        const reasons = Array.isArray(readiness.reasons) ? readiness.reasons : [];
        const qdrantOk = readiness.qdrant?.ok === true;
        const lavaReady = readiness.lava?.ready === true;
        const indexedCount = readiness.active_embedding_index?.indexed_chunk_count || 0;
        const quality = getActiveQuality(payload);
        const qualityOk = quality !== null && quality.query_safe === true;

        appendReadinessCard('SQLite', readiness.sqlite === true, readiness.sqlite === true ? 'OK' : 'Missing');
        appendReadinessCard('Qdrant', qdrantOk, qdrantOk ? 'OK' : 'Unavailable');
        appendReadinessCard('LAVA Tasks', lavaReady, lavaReady ? 'Ready' : 'Blocked');
        appendReadinessCard('Demo Verify', getTaskReady(payload, 'claim_verify'), getTaskReady(payload, 'claim_verify') ? 'Ready' : 'Optional');
        appendReadinessCard('Demo Candidate', getTaskReady(payload, 'synthetic_ebm_candidate'), getTaskReady(payload, 'synthetic_ebm_candidate') ? 'Ready' : 'Optional');
        appendReadinessCard('Active Index', indexedCount > 0, `${indexedCount} chunks`);
        appendReadinessCard('Index Quality', qualityOk, qualityLabel(quality));

        setButtonDisabled(btnRagIndex, shouldDisableIndex(payload));
        setButtonDisabled(btnRagQuery, shouldDisableQuery(payload));

        if (payload.ok === true) {
            setStatusText(ragReadinessStatus, 'RAG ready for query.');
            return;
        }
        const reasonText = reasons.length > 0 ? reasons.join(', ') : 'not_ready';
        setStatusText(ragReadinessStatus, `RAG not ready: ${reasonText}`, true);
    };

    const loadRagReadiness = async () => {
        try {
            const payload = await fetchJson('/api/v1/rag/health');
            renderRagReadiness(payload);
            return payload;
        } catch (error) {
            lastRagReadiness = null;
            setButtonDisabled(btnRagIndex, true);
            setButtonDisabled(btnRagQuery, true);
            setStatusText(ragReadinessStatus, error.message, true);
            renderJson(ragReadinessOutput, error.payload || {
                error: error.message
            });
            if (ragReadinessSummary !== null) {
                ragReadinessSummary.innerHTML = '';
            }
            return null;
        }
    };

    const activateTab = (targetName) => {
        navViewerTab.classList.toggle('active', targetName === 'viewer');
        navRagTab.classList.toggle('active', targetName === 'rag');
        navSystemTab.classList.toggle('active', targetName === 'system');
        tabViewer.classList.toggle('active', targetName === 'viewer');
        tabRag.classList.toggle('active', targetName === 'rag');
        tabSystem.classList.toggle('active', targetName === 'system');
    };

    const applyInitialPaperSelection = () => {
        if (initialPaperSelectionApplied === true || initialPaperId.trim() === '') {
            return;
        }
        const targetPaperId = initialPaperId.trim();
        const matchingOption = Array.from(paperSelect.options).find((option) => option.value === targetPaperId);
        if (matchingOption === undefined) {
            canvas.innerHTML = `
                <div style="color:#92400e; background:#fffbeb; border:1px solid #fde68a; padding:20px; border-radius:6px; font-weight:600;">
                    Requested paper ID was not found in the processed history: ${escapeHtml(targetPaperId)}
                </div>
            `;
            initialPaperSelectionApplied = true;
            return;
        }
        paperSelect.value = targetPaperId;
        if (ragPaperSelect !== null) {
            ragPaperSelect.value = targetPaperId;
        }
        activateTab('viewer');
        initialPaperSelectionApplied = true;
        window.setTimeout(() => {
            btnLoadDetails.click();
        }, 0);
    };

    // ==========================================
    // 1. 分頁切換與歷史清單非同步拉取 (History Loading)
    // ==========================================
    
    /**
     * 向後端請求已處理的歷史文獻清單，並動態組裝成 HTML 表格列注入 DOM 中。
     */
    const loadHistory = async () => {
        try {
            // 執行非同步 API 請求
            const response = await fetch('/api/v1/history');
            const data = await response.json();
            
            // 清空舊有資料
            tbody.innerHTML = '';
            paperSelect.innerHTML = '<option value="">-- Select Processed Paper ID --</option>';
            if (ragPaperSelect !== null) {
                ragPaperSelect.innerHTML = '<option value="">-- Select Paper ID --</option>';
            }

            // 遍歷所有歷史紀錄並建立介面元素
            data.forEach((item) => {
                
                // 1. 動態置入下拉選單 (Select Options)
                const opt = document.createElement('option');
                opt.value = item.paper_id;
                opt.textContent = `${item.filename} (${item.paper_id})`;
                paperSelect.appendChild(opt);

                if (ragPaperSelect !== null) {
                    const ragOpt = document.createElement('option');
                    ragOpt.value = item.paper_id;
                    ragOpt.textContent = `${item.filename} (${item.paper_id})`;
                    ragPaperSelect.appendChild(ragOpt);
                }

                // 2. 判斷處理狀態對應的顏色
                let statusColor = '';
                if (item.status === 'Success') {
                    statusColor = '#059669'; // 綠色
                } else {
                    statusColor = '#dc2626'; // 紅色
                }

                // 3. 垂直展開建置表格行 (Table Row) 結構
                const trHTML = `
                    <tr>
                        <td style="font-weight: 600; color: #1f2937;">
                            ${escapeHtml(item.filename)}
                        </td>
                        <td style="font-family:monospace; color: #6b7280;">
                            ${escapeHtml(item.paper_id)}
                        </td>
                        <td style="font-weight: 500;">
                            ${escapeHtml(item.duration)}
                        </td>
                        <td>
                            <span style="background: ${statusColor}; color: white; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: bold;">
                                ${escapeHtml(String(item.status || '').toUpperCase())}
                            </span>
                        </td>
                        <td>
                            <button class="btn-del-mini" data-id="${escapeHtml(item.paper_id)}">
                                Purge Data
                            </button>
                        </td>
                    </tr>
                `;
                
                // 將 HTML 注入表格容器
                tbody.insertAdjacentHTML('beforeend', trHTML);
            });

            applyInitialPaperSelection();
             
        } catch (err) {
            console.error("Failed to load or parse history list:", err);
        }
    };

    // 綁定 Extraction Viewer 分頁切換事件
    navViewerTab.addEventListener('click', () => {
        activateTab('viewer');
        // 切換至檢閱分頁時，重新載入最新歷史紀錄
        loadHistory();
    });

    navRagTab.addEventListener('click', () => {
        activateTab('rag');
        loadHistory();
        loadRagReadiness();
    });

    // 綁定 System Settings 分頁切換事件
    navSystemTab.addEventListener('click', () => {
        activateTab('system');
    });

    if (btnLiteratureSearch !== null) {
        btnLiteratureSearch.addEventListener('click', runLiteratureSearch);
    }

    if (literatureSearchInput !== null) {
        literatureSearchInput.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') {
                return;
            }
            event.preventDefault();
            runLiteratureSearch();
        });
    }

    const initializeBrandForm = () => {
        if (brandNameInput === null) {
            return;
        }
        const storedBrandName = readLocalStorage(brandStorageKeys.name);
        if (storedBrandName !== null && storedBrandName.trim() !== '') {
            brandNameInput.value = storedBrandName.trim();
        }
    };

    if (adminBrandForm !== null) {
        initializeBrandForm();
        adminBrandForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const brandName = brandNameInput !== null ? brandNameInput.value.trim() : '';
            if (brandName === '') {
                setBrandSaveStatus('Brand name is required.', true);
                return;
            }
            if (brandName.length > 80) {
                setBrandSaveStatus('Brand name must be 80 characters or shorter.', true);
                return;
            }
            setBrandSaveStatus('Saving browser-local configuration');
            try {
                if (writeLocalStorage(brandStorageKeys.name, brandName) === false) {
                    throw new Error('Browser storage is not available.');
                }
                const selectedFile = brandLogoInput !== null && brandLogoInput.files.length > 0 ? brandLogoInput.files[0] : null;
                const logoDataUrl = await readLogoFileAsDataUrl(selectedFile);
                if (logoDataUrl !== null && writeLocalStorage(brandStorageKeys.logo, logoDataUrl) === false) {
                    throw new Error('Logo could not be saved to browser storage.');
                }
                if (brandLogoInput !== null) {
                    brandLogoInput.value = '';
                }
                applyHeaderBrand();
                setBrandSaveStatus('Configuration saved for this browser.');
            } catch (error) {
                setBrandSaveStatus(error.message, true);
            }
        });
    }


    // ==========================================
    // 2. 數據生命週期管理 (Data Lifecycle & Purge)
    // ==========================================
    
    // 利用事件委派 (Event Delegation) 監聽表格內的刪除按鈕
    tbody.addEventListener('click', (event) => {
        // 尋找被點擊元素最近的 .btn-del-mini 類別
        const targetBtn = event.target.closest('.btn-del-mini');
        
        if (targetBtn !== null) {
            // 提取自訂資料屬性 (data-id)
            currentDelId = targetBtn.getAttribute('data-id');
            delIdLabel.textContent = currentDelId;
            
            // 顯示確認視窗
            deleteModal.style.display = 'flex';
        }
    });

    // 綁定刪除取消事件
    btnDeleteCancel.addEventListener('click', () => {
        deleteModal.style.display = 'none';
        currentDelId = null;
    });

    // 綁定確認刪除事件
    btnConfirmDelete.addEventListener('click', async () => {
        if (currentDelId === null) {
            return;
        }
        
        // 防呆：鎖定按鈕防止重複點擊
        btnConfirmDelete.disabled = true;
            btnConfirmDelete.textContent = "Deleting process ongoing";

        try {
            // 執行 DELETE 請求
            const response = await fetch(`/api/v1/paper/${currentDelId}`, { 
                method: 'DELETE' 
            });
            
            if (response.ok === true) {
                // 關閉視窗與重置狀態
                deleteModal.style.display = 'none';
                
                // 檢查目前 Viewer 畫布是否正在預覽即將刪除的文件
                if (paperSelect.value === currentDelId) {
                    paperSelect.value = "";
                    // 重置畫布為初始歡迎文字
                    canvas.innerHTML = `
                        <h3 style="color: #4b5563; margin-top: 0; border-bottom: 2px solid #eee; padding-bottom: 15px;">
                            Data Lifecycle Management
                        </h3>
                    `;
                }
                
                // 刪除成功後重新載入列表
                loadHistory();
            }
        } catch (err) {
            alert("Deletion operation failed due to network or server error.");
        } finally {
            // 無論成功與否，恢復按鈕預設狀態
            currentDelId = null;
            btnConfirmDelete.disabled = false;
            btnConfirmDelete.textContent = "Delete All";
        }
    });


    // ==========================================
    // 3. 雙軌模式重組資料檢閱引擎 (Dual-Mode Review Engine)
    // ==========================================
    
    // 綁定載入特定文獻詳細資料的按鈕
    btnLoadDetails.addEventListener('click', async () => {
        const targetPid = paperSelect.value;
        
        // 若未選擇文件則直接返回
        if (targetPid === "") {
            return;
        }

        // 渲染等待中的提示訊息
        canvas.innerHTML = `
            <div style="text-align: center; padding: 40px; color: #666; font-weight: bold;">
                Invoking lit_collector Engine and aggregating multi-modal payload
            </div>
        `;

        try {
            // 呼叫後端 API 取得重組資料
            const response = await fetch(`/api/v1/viewer/${targetPid}`);
            currentPayloadData = await response.json();
            
            // 第一步：初始化雙軌切換列的 DOM 框架
            renderFramework();
            
            // 第二步：預設執行多模態對位渲染 (Visual View)
            renderVisualView();
            
        } catch (err) {
            canvas.innerHTML = `
                <div style="color: red; padding: 20px; font-weight: bold;">
                    Collector payload integration failed. Server or Parser Error.
                </div>
            `;
        }
    });

    /**
     * 初始化雙軌模式的 UI 骨架，建立按鈕與對應的隱藏容器。
     */
    const renderFramework = () => {
        // 清空主畫布
        canvas.innerHTML = '';

        // 建立切換控制列容器
        const switchBar = document.createElement('div');
        switchBar.className = 'viewer-mode-switch';

        // 建立 Visual 按鈕
        const btnVisual = document.createElement('button');
        btnVisual.className = 'btn-mode active';
        btnVisual.textContent = 'Visual Render View';

        // 建立 Raw Data 按鈕
        const btnRaw = document.createElement('button');
        btnRaw.className = 'btn-mode';
        btnRaw.textContent = 'Raw Data JSON Mode';

        // 將按鈕加入控制列
        switchBar.appendChild(btnVisual);
        switchBar.appendChild(btnRaw);
        canvas.appendChild(switchBar);

        // 建立視覺渲染專用容器
        const visualDiv = document.createElement('div');
        visualDiv.id = 'visual-render-view';
        
        // 建立 JSON 排版專用預格式化標籤容器
        const rawPre = document.createElement('pre');
        rawPre.id = 'raw-json-view';

        canvas.appendChild(visualDiv);
        canvas.appendChild(rawPre);

        // ==========================================
        // 綁定雙軌互斥切換邏輯 (Mutual Exclusion Logic)
        // ==========================================
        
        btnVisual.addEventListener('click', () => {
            // 切換按鈕焦點狀態
            btnVisual.classList.add('active');
            btnRaw.classList.remove('active');
            
            // 切換容器顯示狀態
            visualDiv.style.display = 'block';
            rawPre.style.display = 'none';
        });

        btnRaw.addEventListener('click', () => {
            // 切換按鈕焦點狀態
            btnRaw.classList.add('active');
            btnVisual.classList.remove('active');
            
            // 切換容器顯示狀態
            visualDiv.style.display = 'none';
            rawPre.style.display = 'block';
            
            // 將 API 回傳的巨型字典進行格式化並寫入
            if (currentPayloadData !== null) {
                const jsonString = JSON.stringify(currentPayloadData, null, 4);
                rawPre.textContent = jsonString;
            }
        });
    };

    /**
     * [重構升級] 讀取 Payload 並動態生成高結構化的視覺版面。
     * 導入「左右雙視窗」架構：左側顯示原始裁剪影像，右側提供可編輯的文本區域。
     */
    const renderVisualView = () => {
        // 安全性檢查
        if (currentPayloadData === null) {
            return;
        }
        
        const container = document.getElementById('visual-render-view');
        
        // 渲染文獻大標題
        container.innerHTML = `
            <h2 class="type-title">${currentPayloadData.filename}</h2>
        `;

        const documentStream = Array.isArray(currentPayloadData.document_stream) ? currentPayloadData.document_stream : [];
        if (documentStream.length === 0) {
            container.insertAdjacentHTML('beforeend', '<div style="color:#6b7280; padding:20px;">No Core0 visual document stream nodes are available for this record. Native-text fixtures and metadata-only imports can still be indexed and reviewed from RAG Operations.</div>');
            return;
        }
        
        // 遍歷所有節點，動態生成排版結構
        documentStream.forEach((node) => {
            
            // 建立節點主容器 (.doc-node)
            const nodeDiv = document.createElement('div');
            
            // 處理標籤大小寫，用於 CSS 選擇器綁定
            let safeType = 'text';
            if (node.semantic_type !== null && node.semantic_type !== "") {
                safeType = node.semantic_type.toLowerCase();
            }
            nodeDiv.className = `doc-node type-${safeType}`;

            // 置入除錯邊界標籤 (Debug Boundary Tag)
            const tagSpan = document.createElement('span');
            tagSpan.className = 'node-tag';
            
            // 格式化信賴分數
            let confScore = 0.0;
            if (node.layout_confidence !== null) {
                confScore = node.layout_confidence.toFixed(3);
            }
            
            // 寫入標籤內容
            tagSpan.textContent = `Page: ${node.page_number} | Obj_ID: ${node.object_id} | Label: ${node.semantic_type} | Conf: ${confScore}`;
            nodeDiv.appendChild(tagSpan);

            // 建立左右分割的彈性容器 (.split-node-container)
            const splitContainer = document.createElement('div');
            splitContainer.className = 'split-node-container';

            // ==========================================
            // 構建左視窗 (Left Pane) - 實體影像區
            // ==========================================
            const leftPane = document.createElement('div');
            leftPane.className = 'split-left-pane';

            if (node.image_url !== null && node.image_url !== "") {
                const imgElement = document.createElement('img');
                imgElement.src = node.image_url;
                imgElement.alt = "Extracted Source Crop Figure";
                leftPane.appendChild(imgElement);
            } else {
                // 處理無影像的純文本區塊
                leftPane.innerHTML = `<span style="color: #9ca3af; font-size: 12px; font-style: italic;">No Image Crop Available</span>`;
            }
            
            // 將左視窗加入分割容器
            splitContainer.appendChild(leftPane);

            // ==========================================
            // 構建右視窗 (Right Pane) - 可編輯文本區
            // ==========================================
            const rightPane = document.createElement('div');
            rightPane.className = 'split-right-pane';

            // 建立可編輯的 Textarea 元素
            const textArea = document.createElement('textarea');
            textArea.className = 'editable-textarea';
            
            // 填入既有的 OCR 萃取文本
            if (node.extracted_content !== null && node.extracted_content !== "") {
                textArea.value = node.extracted_content;
            } else {
                textArea.placeholder = "No text extracted by OCR engine. You can manually enter text here.";
            }
            
            // 將編輯區加入右視窗
            rightPane.appendChild(textArea);

            // 建立底部工具列與儲存按鈕
            const toolbarDiv = document.createElement('div');
            toolbarDiv.className = 'pane-toolbar';
            
            const btnSave = document.createElement('button');
            btnSave.className = 'btn-save-edit';
            btnSave.textContent = 'Preview Correction';
            
            btnSave.addEventListener('click', () => {
                const updatedText = textArea.value;
                
                // 變更按鈕狀態給予視覺回饋
                const originalText = btnSave.textContent;
                btnSave.textContent = 'Previewed';
                btnSave.style.background = '#374151';
                
                // 輸出 Log 以驗證機制
                console.log(`[Preview Correction] Paper: ${currentPayloadData.paper_id} | Page: ${node.page_number} | Obj: ${node.object_id}`);
                console.log(`Preview Text Payload:`, updatedText);
                
                // 1 秒後恢復按鈕原狀
                setTimeout(() => {
                    btnSave.textContent = originalText;
                    btnSave.style.background = 'var(--success-green)';
                }, 1000);
            });

            // 將按鈕與工具列加入右視窗
            toolbarDiv.appendChild(btnSave);
            rightPane.appendChild(toolbarDiv);
            
            // 將右視窗加入分割容器
            splitContainer.appendChild(rightPane);

            // ==========================================
            // 最終組裝
            // ==========================================
            // 將分割容器置入主節點容器
            nodeDiv.appendChild(splitContainer);
            
            // 將主節點容器注入畫面畫布
            container.appendChild(nodeDiv);
        });
    };

    const getSelectedRagPaperId = () => {
        if (ragPaperSelect === null) {
            return '';
        }
        return ragPaperSelect.value.trim();
    };

    const clearQuerySummary = () => {
        if (ragQuerySummary !== null) {
            ragQuerySummary.innerHTML = '';
        }
    };

    const appendQuerySummaryRow = (tbodyElement, cells) => {
        const row = document.createElement('tr');
        cells.forEach((value) => {
            const cell = document.createElement('td');
            cell.textContent = value;
            row.appendChild(cell);
        });
        tbodyElement.appendChild(row);
    };

    const renderRagQuerySummary = (payload) => {
        renderJson(queryOutput, payload);
        clearQuerySummary();
        const light = String(payload.light_color || 'yellow').toLowerCase();
        const safeLight = ['green', 'yellow', 'orange'].includes(light) ? light : 'yellow';
        const queryId = payload.query_id || '';
        if (queryId !== '') {
            lastRagQueryId = queryId;
            if (retrievalQueryId !== null) {
                retrievalQueryId.value = queryId;
            }
        }
        if (ragQueryStatus !== null) {
            ragQueryStatus.innerHTML = '';
            const lightBadge = document.createElement('span');
            lightBadge.className = `rag-light ${safeLight}`;
            lightBadge.textContent = safeLight.toUpperCase();
            const statusText = document.createTextNode(payload.short_comment || payload.status || 'Query completed');
            ragQueryStatus.appendChild(lightBadge);
            ragQueryStatus.appendChild(statusText);
        }
        if (ragQuerySummary !== null) {
            const comments = Array.isArray(payload.rag_comments) ? payload.rag_comments : [];
            const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
            const retrieval = isPlainObject(payload.retrieval) ? payload.retrieval : {};
            const queryPlan = isPlainObject(retrieval.query_plan) ? retrieval.query_plan : {};
            const demoVerifier = isPlainObject(payload.demo_verifier)
                ? payload.demo_verifier
                : (isPlainObject(payload.demo_candidate?.ebm_hits?.demo_verifier) ? payload.demo_candidate.ebm_hits.demo_verifier : {});
            const title = document.createElement('div');
            const scoreText = Number.isFinite(Number(payload.llmaaj_score)) ? ` | score=${Number(payload.llmaaj_score)}` : '';
            title.textContent = `status=${payload.status || 'unknown'}${scoreText} | sources=${comments.length} | warnings=${warnings.length}`;
            ragQuerySummary.appendChild(title);

            if (queryId !== '') {
                const queryIdLine = document.createElement('div');
                queryIdLine.style.marginTop = '6px';
                queryIdLine.textContent = `Query ID: ${queryId}`;
                ragQuerySummary.appendChild(queryIdLine);
            }

            if (Object.keys(demoVerifier).length > 0) {
                const verifierLine = document.createElement('div');
                verifierLine.style.marginTop = '6px';
                verifierLine.style.color = demoVerifier.verdict === 'pass' ? '#065f46' : '#92400e';
                const hardFails = Array.isArray(demoVerifier.hard_fail_reasons) && demoVerifier.hard_fail_reasons.length > 0
                    ? ` | hard_fail=${demoVerifier.hard_fail_reasons.join(',')}`
                    : '';
                verifierLine.textContent = `demo_verifier=${demoVerifier.verdict || 'unknown'} | display=${demoVerifier.display_mode || 'unknown'} | verifier_score=${demoVerifier.score ?? 'n/a'}${hardFails}`;
                ragQuerySummary.appendChild(verifierLine);
            }

            if (Object.keys(queryPlan).length > 0) {
                const queryPlanLine = document.createElement('div');
                queryPlanLine.style.marginTop = '6px';
                const totalQueries = Array.isArray(queryPlan.queries) ? queryPlan.queries.length : 0;
                queryPlanLine.textContent = `query_plan=${queryPlan.mode || 'unknown'} | source=${queryPlan.source || 'unknown'} | total_queries=${totalQueries} | llm_status=${queryPlan.llm_status || 'unknown'}`;
                ragQuerySummary.appendChild(queryPlanLine);
                if (queryPlan.fallback_reason) {
                    const fallbackLine = document.createElement('div');
                    fallbackLine.style.marginTop = '4px';
                    fallbackLine.style.color = '#92400e';
                    fallbackLine.textContent = `query_fallback=${queryPlan.fallback_reason}`;
                    ragQuerySummary.appendChild(fallbackLine);
                }
            }

            if (warnings.length > 0) {
                const warningLine = document.createElement('div');
                warningLine.style.marginTop = '6px';
                warningLine.style.color = '#92400e';
                warningLine.textContent = `warnings: ${warnings.map((warning) => warning.code || 'warning').join(', ')}`;
                ragQuerySummary.appendChild(warningLine);
            }

            if (comments.length > 0) {
                const table = document.createElement('table');
                table.className = 'rag-summary-table';
                const thead = document.createElement('thead');
                const headRow = document.createElement('tr');
                ['Topic', 'Evidence', 'Grade', 'Sources'].forEach((label) => {
                    const th = document.createElement('th');
                    th.textContent = label;
                    headRow.appendChild(th);
                });
                thead.appendChild(headRow);
                table.appendChild(thead);
                const tbodyElement = document.createElement('tbody');
                comments.forEach((comment) => {
                    const sources = Array.isArray(comment.sources) ? comment.sources : [];
                    const sourceText = sources.map((source) => source.chunk_id || '').filter(Boolean).join(', ');
                    appendQuerySummaryRow(tbodyElement, [
                        comment.topic || '',
                        comment.evidence_level || 'unknown',
                        comment.grade || 'unknown',
                        sourceText || 'none'
                    ]);
                });
                table.appendChild(tbodyElement);
                ragQuerySummary.appendChild(table);
            }
        }
    };

    if (btnRagIndex !== null) {
        btnRagIndex.addEventListener('click', async () => {
            const paperId = getSelectedRagPaperId();
            if (paperId === '') {
                setStatusText(ragIndexStatus, 'Select a paper first.', true);
                renderValidationError(ragChunksOutput, 'paper_id_required', 'Select a paper before indexing.');
                return;
            }
            btnRagIndex.disabled = true;
            setStatusText(ragIndexStatus, `Indexing ${paperId}`);
            try {
                const readinessPayload = await loadRagReadiness();
                if (shouldDisableIndex(readinessPayload)) {
                    setStatusText(ragIndexStatus, 'Index blocked until SQLite, Qdrant, and embedding_dense are ready.', true);
                    return;
                }
                btnRagIndex.disabled = true;
                const externalMeta = parseJsonInput(ragIndexMetadata, {}, 'external_meta');
                const payload = await fetchJson(`/api/v1/rag/index/${paperId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        external_meta: externalMeta
                    })
                });
                renderJson(ragChunksOutput, payload);
                if (payload.ok === true) {
                    setStatusText(ragIndexStatus, `Index ready for query: ${paperId}.`);
                } else {
                    setStatusText(ragIndexStatus, `Index not ready: ${payload.status || 'not_ready'}.`, true);
                }
                await loadRagReadiness();
            } catch (error) {
                setStatusText(ragIndexStatus, error.message, true);
                renderJson(ragChunksOutput, error.payload || {
                    error: error.message
                });
            } finally {
                setButtonDisabled(btnRagIndex, shouldDisableIndex(lastRagReadiness));
            }
        });
    }

    if (btnRagQuarantineQuality !== null) {
        btnRagQuarantineQuality.addEventListener('click', async () => {
            btnRagQuarantineQuality.disabled = true;
            setStatusText(ragIndexStatus, 'Quarantining failed quality chunks');
            try {
                const payload = await fetchJson('/api/v1/rag/quality/quarantine', {
                    method: 'POST'
                });
                renderJson(ragChunksOutput, payload);
                setStatusText(ragIndexStatus, `Quarantined ${payload.quality_blocked || 0} chunks; ${payload.passed || 0} chunks remained indexed.`);
                await loadRagReadiness();
            } catch (error) {
                setStatusText(ragIndexStatus, error.message, true);
                renderJson(ragChunksOutput, error.payload || {
                    error: error.message
                });
            } finally {
                btnRagQuarantineQuality.disabled = false;
            }
        });
    }

    if (btnRagLoadChunks !== null) {
        btnRagLoadChunks.addEventListener('click', async () => {
            const paperId = getSelectedRagPaperId();
            if (paperId === '') {
                setStatusText(ragIndexStatus, 'Select a paper first.', true);
                renderValidationError(ragChunksOutput, 'paper_id_required', 'Select a paper before loading chunks.');
                return;
            }
            setStatusText(ragIndexStatus, `Loading chunks for ${paperId}`);
            try {
                const payload = await fetchJson(`/api/v1/rag/papers/${paperId}/chunks`);
                renderJson(ragChunksOutput, payload);
                const quality = payload.quality || {};
                const issueCount = Number(quality.issue_chunk_count || 0);
                if (issueCount > 0) {
                    setStatusText(ragIndexStatus, `Loaded ${payload.total || 0} chunks. Quality blocked: ${issueCount} chunks need repair.`, true);
                } else {
                    setStatusText(ragIndexStatus, `Loaded ${payload.total || 0} chunks. Quality safe.`);
                }
            } catch (error) {
                setStatusText(ragIndexStatus, error.message, true);
                renderJson(ragChunksOutput, error.payload || {
                    error: error.message
                });
            }
        });
    }

    if (btnRagQuery !== null) {
        btnRagQuery.addEventListener('click', async () => {
            const dxSummary = queryDxSummary !== null ? queryDxSummary.value.trim() : '';
            if (dxSummary === '') {
                setStatusText(ragQueryStatus, 'dx_summary is required.', true);
                renderValidationError(queryOutput, 'dx_summary_required', 'Enter a dx_summary before running a RAG query.');
                return;
            }
            btnRagQuery.disabled = true;
            setStatusText(ragQueryStatus, 'Running RAG query');
            clearQuerySummary();
            try {
                const readinessPayload = await loadRagReadiness();
                if (shouldDisableQuery(readinessPayload)) {
                    setStatusText(ragQueryStatus, 'RAG is not ready for query. Check readiness details first.', true);
                    renderJson(queryOutput, readinessPayload || {
                        status: 'not_ready'
                    });
                    return;
                }
                const caseContext = parseJsonInput(queryCaseContext, {}, 'case_context');
                const filters = parseJsonInput(queryFilters, {}, 'filters');
                if (!isPlainObject(caseContext)) {
                    throw new Error('case_context JSON must be an object');
                }
                if (!isPlainObject(filters)) {
                    throw new Error('filters JSON must be an object');
                }
                const selectedMode = queryDecompositionMode !== null ? queryDecompositionMode.value : 'deterministic';
                filters.query_decomposition_mode = selectedMode === 'llm_assisted' ? 'llm_assisted' : 'deterministic';
                filters.demo_synthetic_fallback = demoSyntheticFallback !== null && demoSyntheticFallback.checked === true;
                const payload = await fetchJson('/api/v1/rag/query', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        dx_summary: dxSummary,
                        case_context: caseContext,
                        filters: filters,
                        top_k: 10
                    })
                });
                renderRagQuerySummary(payload);
            } catch (error) {
                setStatusText(ragQueryStatus, error.message, true);
                renderJson(queryOutput, error.payload || {
                    error: error.message
                });
            } finally {
                setButtonDisabled(btnRagQuery, shouldDisableQuery(lastRagReadiness));
            }
        });
    }

    if (btnRagRefreshReadiness !== null) {
        btnRagRefreshReadiness.addEventListener('click', async () => {
            await loadRagReadiness();
        });
    }

    if (btnLoadRetrieval !== null) {
        btnLoadRetrieval.addEventListener('click', async () => {
            const queryId = retrievalQueryId !== null ? retrievalQueryId.value.trim() : '';
            const effectiveQueryId = queryId || lastRagQueryId || '';
            if (effectiveQueryId === '') {
                setStatusText(retrievalStatus, 'query_id is required.', true);
                renderValidationError(retrievalOutput, 'query_id_required', 'Enter a query_id or run a RAG query first.');
                return;
            }
            setStatusText(retrievalStatus, `Loading retrieval log ${effectiveQueryId}`);
            try {
                const payload = await fetchJson(`/api/v1/rag/retrieval/${effectiveQueryId}`);
                renderJson(retrievalOutput, payload);
                setStatusText(retrievalStatus, 'Retrieval log loaded.');
            } catch (error) {
                setStatusText(retrievalStatus, error.message, true);
                renderJson(retrievalOutput, error.payload || {
                    error: error.message
                });
            }
        });
    }

    // 系統初始化時，自動觸發一次歷史清單載入
    loadHistory();
    loadRagReadiness();
});
