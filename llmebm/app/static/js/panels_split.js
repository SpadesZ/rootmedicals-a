/*
 * 檔案路徑: rootmedicals-a/llmebm/app/static/js/panels_split.js
 * 產生時間: 2026-06-17 16:10 +08:00
 * 版本: v0.1-交付整理
 * 說明: llmebm 分欄互動與 Medpilot 唯讀單輪 EBM 問答 UI。
 * 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
 * ----------------------------------------------------------------------------------------------------
 */

/*
# 路徑: rootmedicals-a/llmebm/app/static/js/panels_split.js
# 版本: v0.5
# 更版時間: 2026-05-07 12:46
# 說明: 
#   1. 嚴格遵守第二定律：完全保留 v0.3 原有左右欄位控制與所有防呆註解。
#   2. 保留原有的 Medpilot-fab 與 floating-chat-widget ID 選取，新增 medpilot-fab 與 llm_qa_window 的相容綁定。
# ----------------------------------------------------------------------------------------------------
*/

document.addEventListener('DOMContentLoaded', function() {
    
    // ==========================================
    // 區塊 1: 基礎欄位拖曳與縮放 (原 v0.2 邏輯保留)
    // ==========================================
    const resizerLeft = document.getElementById('resizer-left');
    const resizerRight = document.getElementById('resizer-right');
    const panelLeft = document.getElementById('panel-left');
    const panelMiddle = document.getElementById('panel-middle');
    const panelRight = document.getElementById('panel-right');
    const container = document.querySelector('.split-layout');

    if (panelLeft && panelMiddle) {
        panelLeft.style.width = '20%';
        panelMiddle.style.flex = '1 1 0%';
    }

    let isResizing = false;
    let currentResizer = null;
    const MIN_DRAG_PERCENT = 5;

    if (resizerLeft) {
        resizerLeft.addEventListener('mousedown', function(e) {
            isResizing = true;
            currentResizer = 'left';
            resizerLeft.classList.add('active');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
        });
    }

    // 防呆保留：防止舊版右側拖曳桿報錯
    if (resizerRight) {
        resizerRight.addEventListener('mousedown', function(e) {
            isResizing = true;
            currentResizer = 'right';
            document.body.style.userSelect = 'none';
        });
    }

    document.addEventListener('mousemove', function(e) {
        if (!isResizing || !container) return;
        const containerRect = container.getBoundingClientRect();
        const totalWidth = containerRect.width;

        if (currentResizer === 'left') {
            let newLeftWidth = ((e.clientX - containerRect.left) / totalWidth) * 100;
            newLeftWidth = Math.max(MIN_DRAG_PERCENT, Math.min(newLeftWidth, 80));
            panelLeft.style.width = `${newLeftWidth}%`;
        }
    });

    document.addEventListener('mouseup', function() {
        if (isResizing) {
            isResizing = false;
            currentResizer = null;
            if (resizerLeft) resizerLeft.classList.remove('active');
            document.body.style.cursor = 'default';
            document.body.style.userSelect = 'auto';
        }
    });

    // 縮放按鈕邏輯保留
    const btns = document.querySelectorAll('.btn-minimize-toggle');
    btns.forEach(btn => {
        btn.addEventListener('click', function() {
            const targetId = this.getAttribute('data-target');
            const targetPanel = document.getElementById(targetId);
            if (!targetPanel) return;

            const isMinimized = targetPanel.classList.contains('is-minimized');
            if (!isMinimized) {
                targetPanel.setAttribute('data-saved-width', targetPanel.style.width || targetPanel.style.flex);
                if (targetId === 'panel-middle') {
                    targetPanel.style.flex = '0 0 45px';
                } else {
                    targetPanel.style.width = '45px';
                }
                targetPanel.classList.add('is-minimized');
                this.textContent = '[+]';
            } else {
                if (targetId === 'panel-middle') {
                    targetPanel.style.flex = targetPanel.getAttribute('data-saved-width') || '1 1 0%';
                } else {
                    targetPanel.style.width = targetPanel.getAttribute('data-saved-width') || '20%';
                }
                targetPanel.classList.remove('is-minimized');
                this.textContent = '[-]';
            }
        });
    });

    // ==========================================
    // 區塊 2: [新增] 懸浮視窗交互邏輯 (Floating Widget)
    // ==========================================
    
    // [擴充] 同時選取舊版與新版 ID，確保絕對相容不報錯
    const fab = document.getElementById('Medpilot-fab') || document.getElementById('medpilot-fab');
    const widget = document.getElementById('floating-chat-widget') || document.getElementById('llm_qa_window');
    const btnClose = document.getElementById('btn-close-widget');
    const header = document.getElementById('chat-widget-header');

    // 1. 顯示 / 隱藏切換
    if (fab && widget && btnClose) {
        fab.addEventListener('click', () => {
            widget.style.display = 'flex';
            fab.style.display = 'none'; // 開啟視窗時隱藏 FAB
        });

        btnClose.addEventListener('click', () => {
            widget.style.display = 'none';
            fab.style.display = 'block'; // 關閉視窗時恢復 FAB
        });
    }

    // 2. 視窗拖曳 (Draggable) 邏輯
    let isWidgetDragging = false;
    let dragStartX, dragStartY, widgetInitialX, widgetInitialY;

    if (header && widget) {
        header.addEventListener('mousedown', (e) => {
            // 避免點擊關閉按鈕時觸發拖曳
            if (e.target.tagName.toLowerCase() === 'button') return;
            
            isWidgetDragging = true;
            dragStartX = e.clientX;
            dragStartY = e.clientY;
            
            // 取得視窗當前絕對位置
            widgetInitialX = widget.offsetLeft;
            widgetInitialY = widget.offsetTop;
            
            document.body.style.userSelect = 'none'; // 防止拖曳時反白文字
        });

        document.addEventListener('mousemove', (e) => {
            if (!isWidgetDragging) return;
            
            const dx = e.clientX - dragStartX;
            const dy = e.clientY - dragStartY;
            
            // 覆寫 bottom/right 定位，改由 left/top 絕對控制
            widget.style.bottom = 'auto';
            widget.style.right = 'auto';
            widget.style.left = `${widgetInitialX + dx}px`;
            widget.style.top = `${widgetInitialY + dy}px`;
        });

        document.addEventListener('mouseup', () => {
            if (isWidgetDragging) {
                isWidgetDragging = false;
                document.body.style.userSelect = 'auto';
            }
        });
    }

    // ==========================================
    // 區塊 3: Medpilot 單輪 EBM 問答；所有動態文字使用 textContent，citation 只採後端驗證結果。
    // ==========================================
    const btnSendFloat = document.getElementById('btn-send-chat-float');
    const inputFloat = document.getElementById('llm-chat-input-float');
    const historyFloat = document.getElementById('chat-history-float');

    if (btnSendFloat && inputFloat && historyFloat) {
        const scrollHistory = () => { historyFloat.scrollTop = historyFloat.scrollHeight; };

        const appendMessage = (role, text, className = 'system-msg') => {
            const message = document.createElement('div');
            message.className = `chat-msg ${className}`;
            const label = document.createElement('strong');
            label.textContent = `${role}: `;
            message.appendChild(label);
            message.appendChild(document.createTextNode(text));
            historyFloat.appendChild(message);
            scrollHistory();
            return message;
        };

        const appendEvidenceAnswer = (payload) => {
            const message = document.createElement('div');
            message.className = `chat-msg system-msg medpilot-${payload.kind || 'answer'}`;
            const label = document.createElement('strong');
            label.textContent = 'Medpilot: ';
            message.appendChild(label);
            message.appendChild(document.createTextNode(payload.answer || 'No answer was returned.'));

            (payload.sections || []).forEach(section => {
                const heading = document.createElement('h4');
                heading.textContent = section.topic || 'Evidence summary';
                const body = document.createElement('p');
                body.textContent = section.text || '';
                const meta = document.createElement('div');
                meta.className = 'chat-meta';
                const refs = (section.citation_indexes || []).map(index => `[${index}]`).join(' ');
                meta.textContent = `${section.evidence_level || 'unknown'} · ${section.grade || 'unknown'} ${refs}`.trim();
                message.append(heading, body, meta);
            });

            if ((payload.citations || []).length) {
                const citationHeading = document.createElement('h4');
                citationHeading.textContent = 'Sources';
                const list = document.createElement('ol');
                list.className = 'chat-citations';
                payload.citations.forEach(citation => {
                    const item = document.createElement('li');
                    const identifiers = [
                        citation.paper_id,
                        citation.chunk_id,
                        citation.doi ? `DOI ${citation.doi}` : '',
                        citation.pmid ? `PMID ${citation.pmid}` : '',
                    ].filter(Boolean).join(' · ');
                    item.textContent = `${citation.title || citation.paper_id} — ${identifiers}`;
                    list.appendChild(item);
                });
                message.append(citationHeading, list);
            }

            if (payload.query_id) {
                const queryMeta = document.createElement('div');
                queryMeta.className = 'chat-meta';
                queryMeta.textContent = `Retrieval ID: ${payload.query_id}`;
                message.appendChild(queryMeta);
            }
            historyFloat.appendChild(message);
            scrollHistory();
        };

        const appendRetryableError = (text, query) => {
            const message = appendMessage('Medpilot', text, 'system-msg chat-error');
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'chat-retry';
            retry.textContent = 'Retry';
            retry.addEventListener('click', () => {
                inputFloat.value = query;
                submitQuery();
            });
            message.appendChild(retry);
        };

        const submitQuery = async () => {
            const text = inputFloat.value.trim();
            if (!text || btnSendFloat.disabled) return;

            appendMessage('You', text, 'user-msg');
            inputFloat.value = '';
            btnSendFloat.disabled = true;
            inputFloat.disabled = true;
            const pending = appendMessage('Medpilot', 'Retrieving and verifying local evidence…', 'system-msg chat-pending');
            const controller = new AbortController();
            const timeoutId = window.setTimeout(() => controller.abort(), 125000);

            try {
                const response = await fetch('/api/v1/medpilot/query', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                    body: JSON.stringify({query: text}),
                    signal: controller.signal,
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
                pending.remove();
                appendEvidenceAnswer(payload);
            } catch (error) {
                pending.remove();
                const unavailable = !navigator.onLine
                    ? 'The browser is offline. Reconnect before retrying.'
                    : error.name === 'AbortError'
                        ? 'The evidence query timed out. Please retry.'
                        : 'Evidence retrieval is temporarily unavailable. Please retry.';
                appendRetryableError(unavailable, text);
            } finally {
                window.clearTimeout(timeoutId);
                btnSendFloat.disabled = false;
                inputFloat.disabled = false;
                inputFloat.focus();
            }
        };

        btnSendFloat.addEventListener('click', submitQuery);

        // 支援 Enter 鍵快速送出 (Shift+Enter 換行)
        inputFloat.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                btnSendFloat.click();
            }
        });
    }
});
