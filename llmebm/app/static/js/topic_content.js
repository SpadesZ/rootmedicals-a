/*
 * 模組定位: llmebm-topic-content.v1 的瀏覽器端 renderer 與 Topic 閱讀導覽。
 * 主要責任: 安全轉換 component JSON，並提供搜尋、深連結、更新、引用與本機使用者工具。
 * 呼叫來源: specialty.html 的 slot click/expand 流程與 renderer contract tests。
 * 輸入契約: 已通過後端 schema gate 的 slot content response；前端仍逐欄防禦。
 * 輸出契約: 只建立固定 DOM；tabs 與 panels 同步 ARIA；Updates 只顯示已有 slot metadata。
 * 安全邊界: 僅用 DOM 節點與純文字 API；未知 block、缺欄位及非字串資料不得變成可執行內容。
 * 維護提醒: 新增 block 或 view 必須同步後端 allowlist、ARIA、CSS 與 contract test。
 * ----------------------------------------------------------------------------------------------------
 */
(function (global) {
    'use strict';

    const ALLOWED_BLOCKS = new Set([
        'summary', 'recommendations', 'bullets', 'evidence_note', 'table', 'warning'
    ]);
    let topicTabsController = null;
    let topicSlots = [];
    let activeSlotIndex = -1;
    let activeResult = null;
    let statusCache = null;
    let statusHashCache = null;
    let userStateCache = {followed: false, last_seen_status_hash: null};

    function appendTextElement(parent, tagName, text, className) {
        const element = document.createElement(tagName);
        if (className) element.className = className;
        element.textContent = typeof text === 'string' ? text : '';
        parent.appendChild(element);
        return element;
    }

    function renderCitations(parent, citations) {
        if (!Array.isArray(citations) || citations.length === 0) return;
        const list = document.createElement('ul');
        list.className = 'topic-citations';
        list.setAttribute('aria-label', 'Evidence citations');
        citations.forEach((citation) => {
            if (!citation || typeof citation !== 'object') return;
            const paperId = typeof citation.paper_id === 'string' ? citation.paper_id : '';
            const chunkId = typeof citation.chunk_id === 'string' ? citation.chunk_id : '';
            if (!paperId || !chunkId) return;
            appendTextElement(list, 'li', `${paperId} · ${chunkId}`);
        });
        if (list.childNodes.length) parent.appendChild(list);
    }

    function renderTextBlock(parent, block, heading) {
        const section = document.createElement('section');
        section.className = `topic-block topic-block-${block.type}`;
        if (heading) appendTextElement(section, 'h4', heading);
        if (block.type === 'recommendations') {
            const grade = document.createElement('div');
            grade.className = 'recommendation-grades';
            appendTextElement(
                grade, 'span',
                `Strength: ${typeof block.recommendation_strength === 'string' ? block.recommendation_strength : 'not stated'}`,
                'recommendation-grade'
            );
            if (typeof block.evidence_certainty === 'string') {
                appendTextElement(grade, 'span', `Certainty: ${block.evidence_certainty}`, 'recommendation-grade');
            }
            section.appendChild(grade);
        }
        appendTextElement(section, 'p', block.text);
        renderCitations(section, block.citations);
        parent.appendChild(section);
    }

    function renderItemsBlock(parent, block, heading) {
        const section = document.createElement('section');
        section.className = `topic-block topic-block-${block.type}`;
        appendTextElement(section, 'h4', heading);
        const list = document.createElement('ul');
        (Array.isArray(block.items) ? block.items : []).forEach((item) => {
            const normalized = typeof item === 'string' ? {text: item} : item;
            if (!normalized || typeof normalized.text !== 'string') return;
            const listItem = document.createElement('li');
            appendTextElement(listItem, 'span', normalized.text);
            renderCitations(listItem, normalized.citations);
            list.appendChild(listItem);
        });
        section.appendChild(list);
        parent.appendChild(section);
    }

    function renderTable(parent, block) {
        const section = document.createElement('section');
        section.className = 'topic-block topic-block-table';
        if (typeof block.title === 'string') appendTextElement(section, 'h4', block.title);
        const headers = Array.isArray(block.columns) ? block.columns : [];
        const rows = Array.isArray(block.rows) ? block.rows : [];
        const table = document.createElement('table');
        if (headers.length) {
            const thead = document.createElement('thead');
            const row = document.createElement('tr');
            headers.forEach((header) => appendTextElement(row, 'th', String(header)));
            thead.appendChild(row);
            table.appendChild(thead);
        }
        const tbody = document.createElement('tbody');
        rows.forEach((rowData) => {
            if (!rowData || !Array.isArray(rowData.cells)) return;
            const row = document.createElement('tr');
            rowData.cells.forEach((cell) => {
                appendTextElement(row, 'td', String(cell));
            });
            if (row.lastElementChild) renderCitations(row.lastElementChild, rowData.citations);
            tbody.appendChild(row);
        });
        table.appendChild(tbody);
        section.appendChild(table);
        renderCitations(section, block.citations);
        parent.appendChild(section);
    }

    function renderContent(payload, target) {
        if (!target) throw new Error('Missing Topic content target.');
        target.replaceChildren();
        if (!payload || payload.schema !== 'llmebm-topic-content.v1' || !Array.isArray(payload.blocks)) {
            appendTextElement(target, 'p', 'The Topic content response was invalid.', 'topic-content-error');
            return false;
        }
        payload.blocks.forEach((block) => {
            if (!block || !ALLOWED_BLOCKS.has(block.type)) return;
            if (block.type === 'table') return renderTable(target, block);
            if (block.type === 'bullets') return renderItemsBlock(target, block, 'Key points');
            if (block.type === 'recommendations' && Array.isArray(block.items)) {
                return renderItemsBlock(target, block, 'Recommendations');
            }
            const headings = {
                summary: 'Summary', recommendations: 'Recommendations',
                evidence_note: 'Evidence note', warning: 'Important'
            };
            renderTextBlock(target, block, headings[block.type]);
        });
        if (payload.status === 'insufficient_evidence') {
            appendTextElement(target, 'p', 'Insufficient retrieved evidence for this section.', 'topic-content-warning');
        }
        return true;
    }

    function showState(target, heading, message, className) {
        target.replaceChildren();
        appendTextElement(target, 'h3', heading);
        appendTextElement(target, 'p', message, className);
    }

    function bindTopicTabs(tabs, panels) {
        const tabList = Array.from(tabs || []);
        const panelList = Array.from(panels || []);

        function activate(tabId, focus = false) {
            const activeTab = tabList.find((tab) => tab.id === tabId);
            if (!activeTab) return false;
            tabList.forEach((tab) => {
                const selected = tab === activeTab;
                tab.setAttribute('aria-selected', selected ? 'true' : 'false');
                tab.tabIndex = selected ? 0 : -1;
                tab.classList.toggle('active', selected);
                const panelId = tab.getAttribute('aria-controls');
                const panel = panelList.find((candidate) => candidate.id === panelId);
                if (panel) panel.hidden = !selected;
            });
            if (focus) activeTab.focus();
            return true;
        }

        tabList.forEach((tab, index) => {
            tab.addEventListener('click', () => activate(tab.id));
            tab.addEventListener('keydown', (event) => {
                const keyTargets = {
                    ArrowLeft: (index - 1 + tabList.length) % tabList.length,
                    ArrowRight: (index + 1) % tabList.length,
                    Home: 0,
                    End: tabList.length - 1,
                };
                if (!(event.key in keyTargets)) return;
                event.preventDefault();
                activate(tabList[keyTargets[event.key]].id, true);
            });
        });

        const initiallySelected = tabList.find((tab) => tab.getAttribute('aria-selected') === 'true');
        if (initiallySelected) activate(initiallySelected.id);
        return Object.freeze({activate});
    }

    function bindBackToTop(pane, button, threshold = 320) {
        if (!pane || !button) return null;
        const updateVisibility = () => {
            button.hidden = pane.scrollTop <= threshold;
        };
        pane.addEventListener('scroll', updateVisibility);
        button.addEventListener('click', () => pane.scrollTo({top: 0, behavior: 'smooth'}));
        updateVisibility();
        return updateVisibility;
    }

    function updateStatusMessage(status, workflowStatus = '') {
        if (status === 'ready' && workflowStatus && workflowStatus !== 'published') {
            return 'Generated draft only; medical review and publication are not complete.';
        }
        const messages = {
            ready: 'Published evidence-backed content is available.',
            stale: 'The evidence scope changed; preserved content may no longer be current.',
            insufficient_evidence: 'The latest retrieval did not provide enough evidence for this section.',
            failed: 'The latest update attempt failed; preserved content remains available when present.',
            empty: 'No generated content is available for this section yet.',
        };
        return messages[status] || messages.empty;
    }

    function formatUpdatedAt(value) {
        if (typeof value !== 'string' || !value) return '';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return '';
        return `${parsed.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
    }

    function trustPresentation(node, result = {}) {
        // ponytail: one bounded map keeps buyer-facing labels consistent without adding a status framework.
        const status = typeof node.content_status === 'string'
            ? node.content_status
            : (typeof result.status === 'string' ? result.status : 'empty');
        const workflow = typeof result.workflow_status === 'string' ? result.workflow_status : '';
        const visibility = result.visibility === 'published' ? 'published' : 'draft_preview';
        if (status === 'stale') return {status, workflow, visibility, badge: 'Stale content', review: 'Evidence or page structure changed'};
        if (status === 'insufficient_evidence') return {status, workflow, visibility, badge: 'Insufficient evidence', review: 'More reviewed evidence is required'};
        if (status === 'failed') return {status, workflow, visibility, badge: 'Update failed', review: 'The last safe version remains preserved'};
        if (status === 'empty') return {status, workflow, visibility, badge: 'No content', review: 'This section has not been generated'};
        if (visibility === 'published' || workflow === 'published') return {status, workflow, visibility, badge: 'Published', review: result.reviewed_by ? `Reviewed by ${result.reviewed_by}` : 'Published clinical content'};
        if (workflow === 'review_pending') return {status, workflow, visibility, badge: 'Demo preview', review: 'Clinical review pending'};
        if (workflow === 'approved') return {status, workflow, visibility, badge: 'Approved draft', review: result.reviewed_by ? `Reviewed by ${result.reviewed_by}; publication pending` : 'Publication pending'};
        if (workflow === 'rejected') return {status, workflow, visibility, badge: 'Rejected draft', review: 'Revision required'};
        return {status, workflow, visibility, badge: 'Demo preview', review: 'Not medically reviewed'};
    }

    function renderTrustHeader(node, result = {}) {
        const target = document.getElementById('topic-trust-header');
        if (!target || !node) return;
        const view = trustPresentation(node, result);
        target.replaceChildren();
        const badge = appendTextElement(target, 'span', view.badge, 'topic-trust-badge');
        badge.id = 'topic-trust-badge';
        const details = [];
        if (Number.isInteger(result.version_id)) details.push(`Version ${result.version_id}`);
        const updatedAt = formatUpdatedAt(result.updated_at);
        if (updatedAt) details.push(`Updated ${updatedAt}`);
        details.push(view.review);
        appendTextElement(target, 'span', details.join(' · '));
        target.dataset.contentStatus = view.status;
        target.dataset.workflowStatus = view.workflow;
        target.dataset.visibility = view.visibility;
    }

    function renderUpdateStatus(node, result = {}) {
        const target = document.getElementById('topic-updates-content');
        if (!target || !node) return;
        // ponytail: sidebar status already includes structure/evidence stale checks; the content row does not.
        const status = typeof node.content_status === 'string'
            ? node.content_status
            : (typeof result.status === 'string' ? result.status : 'empty');
        target.replaceChildren();
        appendTextElement(target, 'h3', node.name);
        appendTextElement(
            target, 'p', updateStatusMessage(status, result.workflow_status),
            `topic-update-state status-${status}`
        );
        const updatedAt = formatUpdatedAt(result.updated_at);
        if (updatedAt) appendTextElement(target, 'p', `Last stored update: ${updatedAt}`, 'topic-update-time');
        target.dataset.slotId = node.slot_id;
        target.dataset.contentStatus = status;
    }

    function draftWarning(result = {}) {
        const workflow = typeof result.workflow_status === 'string' ? result.workflow_status : '';
        if (workflow === 'approved') return 'Clinically reviewed draft — approved; publication pending.';
        if (workflow === 'review_pending') return 'Internal generated draft — clinical review pending.';
        if (workflow === 'rejected') return 'Rejected draft — revision is required before review or publication.';
        return 'Internal generated draft — not medically reviewed or published.';
    }

    function flattenTopicTree(tree) {
        const flattened = [];
        function walk(nodes, parentPath = []) {
            (Array.isArray(nodes) ? nodes : []).forEach((node) => {
                if (!node || typeof node.slot_id !== 'string') return;
                const path = [...parentPath, String(node.name || '')];
                node.heading_path = path;
                flattened.push(node);
                walk(node.children, path);
            });
        }
        walk(tree && tree.universal);
        walk(tree && tree.custom);
        return flattened;
    }

    function setActionStatus(message) {
        const target = document.getElementById('topic-action-status');
        if (target) target.textContent = message || '';
    }

    function updateReadingNavigation() {
        const previous = document.getElementById('btn-topic-previous');
        const next = document.getElementById('btn-topic-next');
        if (previous) {
            previous.disabled = activeSlotIndex <= 0;
            previous.textContent = activeSlotIndex > 0
                ? `← ${topicSlots[activeSlotIndex - 1].name}` : '← Previous';
        }
        if (next) {
            next.disabled = activeSlotIndex < 0 || activeSlotIndex >= topicSlots.length - 1;
            next.textContent = activeSlotIndex >= 0 && activeSlotIndex < topicSlots.length - 1
                ? `${topicSlots[activeSlotIndex + 1].name} →` : 'Next →';
        }
    }

    function updateDeepLink(slotId) {
        if (!global.history || typeof global.history.replaceState !== 'function') return;
        const base = `${global.location.pathname}${global.location.search}`;
        global.history.replaceState(null, '', `${base}#slot=${encodeURIComponent(slotId)}`);
    }

    function renderRelatedTopics(node) {
        const target = document.getElementById('related-topics-content');
        if (!target) return;
        target.replaceChildren();
        if (!node) {
            appendTextElement(target, 'p', 'Select a section to view related sections.');
            return;
        }
        const rootHeading = Array.isArray(node.heading_path) ? node.heading_path[0] : '';
        const related = topicSlots.filter((candidate) => (
            candidate !== node
            && Array.isArray(candidate.heading_path)
            && candidate.heading_path[0] === rootHeading
            && candidate.layer >= node.layer
        )).slice(0, 6);
        appendTextElement(target, 'p', `Sections in ${rootHeading}:`);
        if (!related.length) {
            appendTextElement(target, 'p', 'No related sections are available.');
            return;
        }
        const list = document.createElement('ul');
        list.className = 'related-topic-list';
        related.forEach((candidate) => {
            const item = document.createElement('li');
            const button = appendTextElement(item, 'button', candidate.name);
            button.type = 'button';
            button.addEventListener('click', () => selectSlot(candidate.button, candidate));
            list.appendChild(item);
        });
        target.appendChild(list);
    }

    function collectCitations(result) {
        const unique = new Map();
        const blocks = result && result.content && Array.isArray(result.content.blocks)
            ? result.content.blocks : [];
        const add = (citations) => (Array.isArray(citations) ? citations : []).forEach((citation) => {
            if (!citation || typeof citation.paper_id !== 'string' || typeof citation.chunk_id !== 'string') return;
            unique.set(`${citation.paper_id}\u0000${citation.chunk_id}`, citation);
        });
        blocks.forEach((block) => {
            add(block.citations);
            (Array.isArray(block.items) ? block.items : []).forEach((item) => add(item.citations));
            (Array.isArray(block.rows) ? block.rows : []).forEach((row) => add(row.citations));
        });
        return Array.from(unique.values());
    }

    function renderReferenceDrawer(result) {
        const target = document.getElementById('topic-reference-content');
        if (!target) return;
        target.replaceChildren();
        const citations = collectCitations(result);
        if (!citations.length) {
            appendTextElement(target, 'p', 'No cited evidence is stored for this section.');
            return;
        }
        const list = document.createElement('ol');
        list.className = 'reference-list';
        citations.forEach((citation) => {
            appendTextElement(list, 'li', `${citation.paper_id} · ${citation.chunk_id}`);
        });
        target.appendChild(list);
        enrichReferenceDrawer(result, citations, target);
    }

    function appendSourceDetail(list, citation, source) {
        const item = document.createElement('li');
        appendTextElement(item, 'strong', source && source.title ? source.title : citation.paper_id);
        appendTextElement(item, 'div', `${citation.paper_id} · ${citation.chunk_id}`, 'source-detail-id');
        const fields = [
            ['Organization', source && source.organization],
            ['Publication year', source && source.publication_year ? String(source.publication_year) : ''],
            ['DOI', source && source.doi],
            ['Document version', source && source.document_version],
            ['Source status', source && source.lifecycle_status],
            ['License status', source && source.license_status],
            ['Commercial publication', source && source.commercial_publication_allowed ? 'allowed' : 'not approved'],
        ];
        fields.forEach(([label, value]) => {
            if (typeof value === 'string' && value) {
                appendTextElement(item, 'div', `${label}: ${value}`, 'source-detail-field');
            }
        });
        list.appendChild(item);
    }

    async function enrichReferenceDrawer(result, citations, target) {
        const article = document.getElementById('main-reading-content');
        const topicName = document.body.dataset.topicName || '';
        const slotId = article && article.dataset.activeSlotId ? article.dataset.activeSlotId : '';
        const paperIds = Array.from(new Set(citations.map((citation) => citation.paper_id)));
        if (!slotId || !paperIds.length) return;
        try {
            const response = await fetch(
                `/api/v1/topic/${encodeURIComponent(topicName)}/content/${encodeURIComponent(slotId)}/source-details`,
                {
                    method: 'POST',
                    headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
                    body: JSON.stringify({paper_ids: paperIds}),
                }
            );
            const payload = await response.json();
            if (activeResult !== result || !response.ok || payload.schema !== 'rootmedicals-source-details.v1') return;
            const sources = new Map(
                (Array.isArray(payload.sources) ? payload.sources : [])
                    .filter((source) => source && typeof source.paper_id === 'string')
                    .map((source) => [source.paper_id, source])
            );
            const details = document.createElement('ol');
            details.className = 'reference-list source-detail-list';
            citations.forEach((citation) => appendSourceDetail(details, citation, sources.get(citation.paper_id)));
            target.replaceChildren(details);
        } catch (error) {
            if (activeResult === result) {
                appendTextElement(target, 'p', 'Source metadata is temporarily unavailable.', 'topic-content-warning');
            }
        }
    }

    function statusCounts(payload) {
        const counts = {ready: 0, stale: 0, insufficient_evidence: 0, failed: 0, empty: 0};
        Object.values((payload && payload.slots) || {}).forEach((value) => {
            const status = value && typeof value.status === 'string' ? value.status : 'empty';
            counts[status] = (counts[status] || 0) + 1;
        });
        return counts;
    }

    function updateAlertCount(payload, followed = true) {
        const target = document.getElementById('topic-alert-count');
        if (!target) return;
        const counts = statusCounts(payload);
        const alreadySeen = statusHashCache
            && userStateCache.last_seen_status_hash === statusHashCache;
        target.textContent = String(followed && !alreadySeen ? counts.stale + counts.failed : 0);
    }

    async function statusFingerprint(payload) {
        if (!payload || !global.crypto || !global.crypto.subtle || typeof TextEncoder === 'undefined') return null;
        const slots = Object.entries(payload.slots || {})
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([slotId, value]) => [slotId, value && value.status, value && value.updated_at]);
        const evidence = Object.entries(payload.evidence_revisions || {})
            .sort(([left], [right]) => left.localeCompare(right));
        const encoded = new TextEncoder().encode(JSON.stringify({slots, evidence}));
        const digest = await global.crypto.subtle.digest('SHA-256', encoded);
        return `sha256:${Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
    }

    async function refreshUpdates() {
        const target = document.getElementById('topic-updates-content');
        const topicName = document.body.dataset.topicName || '';
        if (!target) return;
        target.replaceChildren();
        appendTextElement(target, 'p', 'Loading topic update status…');
        try {
            const response = await fetch(`/api/v1/topic/${encodeURIComponent(topicName)}/content/status`);
            const payload = await response.json();
            if (!response.ok || payload.status !== 'success') throw new Error('status unavailable');
            statusCache = payload;
            statusHashCache = await statusFingerprint(payload);
            updateAlertCount(payload, userStateCache.followed);
            const counts = statusCounts(payload);
            target.replaceChildren();
            appendTextElement(target, 'h3', 'Topic updates');
            const countRow = document.createElement('div');
            countRow.className = 'topic-status-counts';
            Object.entries(counts).forEach(([status, count]) => {
                appendTextElement(countRow, 'span', `${status.replace('_', ' ')}: ${count}`, 'topic-status-pill');
            });
            target.appendChild(countRow);
            appendTextElement(target, 'h4', 'Latest stored section changes');
            const changed = topicSlots.filter((node) => {
                const value = payload.slots && payload.slots[node.slot_id];
                return value && value.status !== 'empty';
            }).sort((left, right) => {
                const leftTime = Date.parse(payload.slots[left.slot_id].updated_at || '') || 0;
                const rightTime = Date.parse(payload.slots[right.slot_id].updated_at || '') || 0;
                return rightTime - leftTime;
            });
            if (!changed.length) {
                appendTextElement(target, 'p', 'No stored section updates are available.');
                return;
            }
            const list = document.createElement('ul');
            list.className = 'topic-update-list';
            changed.forEach((node) => {
                const item = document.createElement('li');
                const value = payload.slots[node.slot_id];
                const button = appendTextElement(item, 'button', `${node.name} — ${value.status}`);
                button.type = 'button';
                button.addEventListener('click', () => selectSlot(node.button, node));
                const metadata = [
                    value.workflow_status ? value.workflow_status.replace('_', ' ') : '',
                    Number.isInteger(value.version_id) ? `Version ${value.version_id}` : '',
                    formatUpdatedAt(value.updated_at),
                ].filter(Boolean).join(' · ');
                if (metadata) appendTextElement(item, 'span', ` · ${metadata}`, 'topic-update-time');
                list.appendChild(item);
            });
            target.appendChild(list);
        } catch (error) {
            target.replaceChildren();
            appendTextElement(target, 'h3', 'Topic updates');
            appendTextElement(target, 'p', 'Update status is temporarily unavailable.', 'topic-content-error');
        }
    }

    function renderToolResults(target, nodes, onSelect) {
        if (!target) return;
        target.replaceChildren();
        nodes.forEach((node) => {
            const item = document.createElement('li');
            const button = appendTextElement(item, 'button', node.label || node.name || node.heading || '');
            button.type = 'button';
            button.addEventListener('click', () => onSelect(node));
            item.appendChild(button);
            target.appendChild(item);
        });
        target.hidden = nodes.length === 0;
    }

    function bindFindInTopic() {
        const input = document.getElementById('topic-find-input');
        const count = document.getElementById('topic-find-count');
        const results = document.getElementById('topic-find-results');
        const previous = document.getElementById('btn-topic-find-prev');
        const next = document.getElementById('btn-topic-find-next');
        if (!input || !count || !results) return;
        let matches = [];
        let cursor = -1;
        const activate = (index) => {
            if (!matches.length) return;
            cursor = (index + matches.length) % matches.length;
            count.textContent = `${cursor + 1} of ${matches.length} matches`;
            const node = matches[cursor];
            selectSlot(node.button, node);
            if (node.button && typeof node.button.scrollIntoView === 'function') {
                node.button.scrollIntoView({block: 'center'});
            }
        };
        input.addEventListener('input', () => {
            const query = input.value.trim().toLocaleLowerCase();
            matches = query ? topicSlots.filter((node) => String(node.name).toLocaleLowerCase().includes(query)) : [];
            cursor = -1;
            count.textContent = `${matches.length} matches`;
            renderToolResults(results, matches, (node) => {
                results.hidden = true;
                activate(matches.indexOf(node));
            });
        });
        if (previous) previous.addEventListener('click', () => activate(cursor - 1));
        if (next) next.addEventListener('click', () => activate(cursor + 1));
    }

    function bindGlobalSearch() {
        const input = document.getElementById('topic-global-search');
        const results = document.getElementById('topic-global-search-results');
        if (!input || !results) return;
        let requestNumber = 0;
        input.addEventListener('input', async () => {
            const query = input.value.trim();
            const thisRequest = ++requestNumber;
            if (query.length < 2) {
                results.hidden = true;
                results.replaceChildren();
                return;
            }
            try {
                const response = await fetch(`/api/v1/search?q=${encodeURIComponent(query)}&limit=20`);
                const payload = await response.json();
                if (thisRequest !== requestNumber) return;
                const rows = response.ok && Array.isArray(payload.results) ? payload.results : [];
                renderToolResults(results, rows.map((row) => ({
                    ...row, label: `${row.heading} — ${row.topic_name}`,
                })), (row) => {
                    global.location.href = `/topic/${encodeURIComponent(row.topic_name)}#slot=${encodeURIComponent(row.slot_id)}`;
                });
            } catch (error) {
                if (thisRequest === requestNumber) results.hidden = true;
            }
        });
    }

    async function copyText(text) {
        if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
            await global.navigator.clipboard.writeText(text);
            return;
        }
        throw new Error('Clipboard API unavailable');
    }

    function citationText() {
        const topicName = document.body.dataset.topicName || '';
        const heading = activeSlotIndex >= 0 ? topicSlots[activeSlotIndex].name : topicName;
        const date = new Date().toISOString().slice(0, 10);
        return `RootMedicals llmebm. ${heading}. Topic: ${topicName}. Accessed ${date}. ${global.location.href}`;
    }

    function bindTopicActions() {
        const topicName = document.body.dataset.topicName || '';
        const follow = document.getElementById('btn-topic-follow');
        const alerts = document.getElementById('btn-topic-alerts');
        const patient = document.getElementById('btn-topic-patient');
        const print = document.getElementById('btn-topic-print');
        const cite = document.getElementById('btn-topic-cite');
        const share = document.getElementById('btn-topic-share');
        const references = document.getElementById('btn-topic-references');
        const drawer = document.getElementById('topic-references-drawer');
        const dialog = document.getElementById('topic-citation-dialog');
        const citationTarget = document.getElementById('topic-citation-text');
        const paintFollow = () => {
            if (!follow) return;
            follow.setAttribute('aria-pressed', userStateCache.followed ? 'true' : 'false');
            follow.textContent = userStateCache.followed ? 'Following' : 'Follow';
            updateAlertCount(statusCache, userStateCache.followed);
        };
        fetch(`/api/v1/topic/${encodeURIComponent(topicName)}/user-state`)
            .then((response) => response.json())
            .then((payload) => {
                userStateCache = {
                    followed: Boolean(payload.followed),
                    last_seen_status_hash: payload.last_seen_status_hash || null,
                };
                paintFollow();
            })
            .catch(() => setActionStatus('Follow state is temporarily unavailable.'));
        if (follow) follow.addEventListener('click', async () => {
            const nextValue = !userStateCache.followed;
            try {
                const response = await fetch(`/api/v1/topic/${encodeURIComponent(topicName)}/user-state`, {
                    method: 'PUT', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({followed: nextValue}),
                });
                const payload = await response.json();
                if (!response.ok) throw new Error('follow update failed');
                userStateCache = {
                    followed: Boolean(payload.followed),
                    last_seen_status_hash: payload.last_seen_status_hash || null,
                };
                paintFollow();
                setActionStatus(userStateCache.followed ? 'Topic followed.' : 'Topic unfollowed.');
            } catch (error) {
                setActionStatus('Follow state could not be saved.');
            }
        });
        if (alerts) alerts.addEventListener('click', async () => {
            if (topicTabsController) topicTabsController.activate('updates-tab', true);
            await refreshUpdates();
            if (!statusHashCache) {
                setActionStatus('The current alert state could not be fingerprinted.');
                return;
            }
            fetch(`/api/v1/topic/${encodeURIComponent(topicName)}/user-state`, {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mark_seen: true, seen_status_hash: statusHashCache}),
            }).then((response) => response.json()).then((payload) => {
                userStateCache.last_seen_status_hash = payload.last_seen_status_hash || null;
                updateAlertCount(statusCache, userStateCache.followed);
            }).catch(() => setActionStatus('Alert acknowledgement could not be saved.'));
        });
        if (patient) patient.addEventListener('click', () => {
            const node = topicSlots.find((candidate) => candidate.name === 'Patient Information');
            if (node) selectSlot(node.button, node);
            else setActionStatus('Patient Information is not available for this topic.');
        });
        if (print) print.addEventListener('click', () => global.print());
        if (share) share.addEventListener('click', async () => {
            try {
                await copyText(global.location.href);
                setActionStatus('Topic link copied.');
            } catch (error) {
                setActionStatus(`Share this link: ${global.location.href}`);
            }
        });
        if (references && drawer) references.addEventListener('click', () => {
            drawer.hidden = !drawer.hidden;
            references.setAttribute('aria-expanded', drawer.hidden ? 'false' : 'true');
            if (!drawer.hidden) renderReferenceDrawer(activeResult);
        });
        if (cite && dialog && citationTarget) cite.addEventListener('click', () => {
            citationTarget.textContent = citationText();
            if (typeof dialog.showModal === 'function') dialog.showModal();
            else dialog.setAttribute('open', '');
        });
        const closeDialog = document.getElementById('btn-topic-citation-close');
        if (closeDialog && dialog) closeDialog.addEventListener('click', () => {
            if (typeof dialog.close === 'function') dialog.close();
            else dialog.removeAttribute('open');
        });
        const copyCitation = document.getElementById('btn-topic-citation-copy');
        if (copyCitation) copyCitation.addEventListener('click', async () => {
            try { await copyText(citationText()); setActionStatus('Citation copied.'); }
            catch (error) { setActionStatus('Citation is shown in the dialog for manual copy.'); }
        });
    }

    function selectDeepLinkedSlot() {
        const params = new URLSearchParams(global.location.hash.replace(/^#/, ''));
        const slotId = params.get('slot');
        if (!slotId) return false;
        const node = topicSlots.find((candidate) => candidate.slot_id === slotId);
        if (!node) return false;
        selectSlot(node.button, node);
        return true;
    }

    function initTopicTools(root, tree) {
        topicSlots = flattenTopicTree(tree);
        bindFindInTopic();
        bindGlobalSearch();
        bindTopicActions();
        const previous = root.getElementById('btn-topic-previous');
        const next = root.getElementById('btn-topic-next');
        if (previous) previous.addEventListener('click', () => {
            if (activeSlotIndex > 0) selectSlot(topicSlots[activeSlotIndex - 1].button, topicSlots[activeSlotIndex - 1]);
        });
        if (next) next.addEventListener('click', () => {
            if (activeSlotIndex >= 0 && activeSlotIndex < topicSlots.length - 1) {
                selectSlot(topicSlots[activeSlotIndex + 1].button, topicSlots[activeSlotIndex + 1]);
            }
        });
        const updatesTab = root.getElementById('updates-tab');
        if (updatesTab) updatesTab.addEventListener('click', refreshUpdates);
        global.addEventListener('hashchange', selectDeepLinkedSlot);
        updateReadingNavigation();
        refreshUpdates();
        if (!selectDeepLinkedSlot() && topicSlots[0] && topicSlots[0].button) {
            selectSlot(topicSlots[0].button, topicSlots[0]);
        }
    }

    function initNavigation(root = document) {
        topicTabsController = bindTopicTabs(
            root.querySelectorAll('.topic-tabs [role="tab"]'),
            root.querySelectorAll('.topic-tab-panel[role="tabpanel"]')
        );
        bindBackToTop(
            root.getElementById('pane-middle'),
            root.getElementById('btn-topic-back-to-top')
        );
        return topicTabsController;
    }

    async function selectSlot(button, node) {
        const target = document.getElementById('main-reading-content');
        if (!target || !node || !button) return;
        if (topicTabsController) topicTabsController.activate('topic-tab');
        document.querySelectorAll('[data-slot-id][aria-current="true"]').forEach((element) => {
            element.removeAttribute('aria-current');
        });
        button.setAttribute('aria-current', 'true');
        activeSlotIndex = topicSlots.findIndex((candidate) => candidate.slot_id === node.slot_id);
        activeResult = null;
        updateDeepLink(node.slot_id);
        updateReadingNavigation();
        renderRelatedTopics(node);
        renderReferenceDrawer(null);
        target.dataset.activeSlotId = node.slot_id;
        target.setAttribute('aria-busy', 'true');
        renderTrustHeader(node);
        renderUpdateStatus(node);
        showState(target, node.name, 'Loading evidence-backed content…', 'topic-content-loading');
        const topicName = document.body.dataset.topicName || '';
        try {
            const response = await fetch(
                `/api/v1/topic/${encodeURIComponent(topicName)}/content/${encodeURIComponent(node.slot_id)}`,
                {headers: {'Accept': 'application/json'}}
            );
            const result = await response.json();
            if (target.dataset.activeSlotId !== node.slot_id) return;
            renderTrustHeader(node, result);
            renderUpdateStatus(node, result);
            activeResult = result;
            renderReferenceDrawer(result);
            if (response.status === 404 || result.status === 'empty') {
                showState(target, node.name, 'No generated content is available for this section yet.', 'topic-content-empty');
            } else if (!response.ok) {
                showState(target, node.name, 'Content is temporarily unavailable.', 'topic-content-error');
            } else {
                const heading = document.createElement('h3');
                heading.textContent = node.name;
                target.replaceChildren(heading);
                if (result.visibility !== 'published') {
                    appendTextElement(
                        target,
                        'p',
                        draftWarning(result),
                        'topic-content-warning topic-content-draft-warning'
                    );
                }
                const contentRoot = document.createElement('div');
                target.appendChild(contentRoot);
                renderContent(result.content, contentRoot);
            }
        } catch (error) {
            if (target.dataset.activeSlotId === node.slot_id) {
                showState(target, node.name, 'Content is temporarily unavailable.', 'topic-content-error');
            }
        } finally {
            if (target.dataset.activeSlotId === node.slot_id) target.setAttribute('aria-busy', 'false');
        }
    }

    function bindSlotButton(button, node) {
        node.button = button;
        button.addEventListener('click', () => selectSlot(button, node));
    }

    global.TopicContent = Object.freeze({
        bindBackToTop, bindSlotButton, bindTopicTabs, initNavigation, initTopicTools,
        renderContent, renderTrustHeader, renderUpdateStatus, selectSlot
    });
})(window);
