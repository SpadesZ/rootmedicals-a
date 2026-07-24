/*
 * 模組定位: llmebm Medical Review Admin 的安全瀏覽器 controller。
 * 主要責任: 載入 review/generation 狀態，核准精確 mapping、啟動單-slot受控重生，呈現 draft/source gate/audit。
 * 呼叫來源: app/templates/admin.html 的 Medical Review tab。
 * 輸入契約: topic slug、actor、comment、單一候選 slot、manual token 或 dev HttpOnly session 與 server-validated JSON。
 * 輸出契約: 純 DOM review/generation 工作台；所有 mutation 仍由後端 scope/revision/transition gates 決定。
 * 安全邊界: 不使用 HTML 字串注入；dev token 不進 JS，manual token 只寫 localStorage，generation token 永不進瀏覽器。
 * 維護提醒: 新增 workflow action 時同步後端狀態機、contract tests、ARIA 與 browser user-flow gate。
 * ----------------------------------------------------------------------------------------------------
 */
(function () {
    'use strict';

    const REVIEW_TOKEN_STORAGE_KEY = 'llmebm.review.token';
    const state = {
        items: [],
        selectedSlotId: '',
        detail: null,
        manifest: null,
        scopePayload: null,
        contentStatus: null,
        automationStatus: null,
        generationCandidates: [],
    };

    function byId(id) {
        return document.getElementById(id);
    }

    function clear(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    function addText(parent, tag, value, className) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = String(value == null ? '' : value);
        parent.appendChild(element);
        return element;
    }

    function detailText(value) {
        if (typeof value === 'string') return value;
        if (value == null) return '';
        try {
            return JSON.stringify(value);
        } catch (_error) {
            return String(value);
        }
    }

    function setStatus(message, statusState) {
        const surface = byId('review-status');
        if (!surface) return;
        surface.textContent = message;
        surface.dataset.state = statusState || 'neutral';
    }

    function setGenerationJob(message, statusState) {
        const surface = byId('review-generation-job');
        if (!surface) return;
        surface.textContent = message;
        surface.dataset.state = statusState || 'neutral';
    }

    function loadSavedReviewToken() {
        try {
            return localStorage.getItem(REVIEW_TOKEN_STORAGE_KEY) || '';
        } catch (_error) {
            return '';
        }
    }

    function saveReviewToken(token) {
        try {
            // ponytail: localStorage is a local-development shortcut; production replaces it with an HttpOnly RBAC session.
            localStorage.setItem(REVIEW_TOKEN_STORAGE_KEY, token);
        } catch (_error) {
            // Storage denial must not break an otherwise authorized review request.
        }
    }

    function devReviewSessionEnabled() {
        return byId('tab-medical-review').dataset.devReviewSession === 'true';
    }

    function reviewHeaders(extra) {
        const token = byId('review-token-input').value.trim();
        if (!token && !devReviewSessionEnabled()) throw new Error('Review token is required.');
        const headers = Object.assign({}, extra || {});
        if (token) headers['X-LLMEBM-Review-Token'] = token;
        return headers;
    }

    async function apiJson(path, options) {
        const request = Object.assign({}, options || {});
        request.headers = reviewHeaders(request.headers);
        const response = await fetch(path, request);
        let payload = null;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = null;
        }
        if (!response.ok) {
            const detail = payload && (payload.detail || payload.message);
            throw new Error(detailText(detail) || `Request failed with HTTP ${response.status}.`);
        }
        return payload;
    }

    function topicSlug() {
        const topic = byId('review-topic-input').value.trim().toLowerCase();
        if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(topic)) {
            throw new Error('Topic slug must contain lowercase letters, numbers, and hyphens only.');
        }
        return topic;
    }

    function actorName() {
        const actor = byId('review-actor-input').value.trim();
        if (!actor || actor.length > 120) throw new Error('Reviewer identity is required (maximum 120 characters).');
        return actor;
    }

    function filteredItems() {
        const filter = byId('review-status-filter').value;
        return filter === 'all' ? state.items : state.items.filter(item => item.workflow_status === filter);
    }

    function renderQueue() {
        const queue = byId('review-queue');
        const items = filteredItems();
        clear(queue);
        byId('review-queue-count').textContent = String(items.length);
        if (!items.length) {
            addText(queue, 'p', 'No current-ready slots match this workflow filter.', 'review-queue-meta');
            return;
        }
        items.forEach(item => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'review-queue-item';
            button.dataset.slotId = item.slot_id;
            button.setAttribute('aria-current', String(item.slot_id === state.selectedSlotId));
            addText(button, 'strong', item.heading || item.slot_id);
            addText(button, 'span', (item.heading_path || []).join(' › '), 'review-queue-meta');
            addText(
                button,
                'span',
                `${item.workflow_status} · version ${item.version_id} · ${item.paper_ids.length} source(s)`,
                'review-queue-meta',
            );
            button.addEventListener('click', () => loadDetail(item.slot_id));
            queue.appendChild(button);
        });
    }

    function renderScopeReviewSummary(payload) {
        const container = byId('review-scope-summary');
        clear(container);
        const requested = Number(payload && payload.requested) || 0;
        const reviewed = Number(payload && payload.reviewed) || 0;
        const current = Number(payload && payload.current_approved) || 0;
        addText(
            container,
            'p',
            `${current}/${reviewed} reviewed mappings current · ${requested} topic slots checked`,
            reviewed > 0 && current === reviewed ? 'review-gate-allowed' : 'review-gate-blocked',
        );
        const items = payload && Array.isArray(payload.items) ? payload.items : [];
        if (!items.length) {
            addText(container, 'p', 'No evidence mapping reviews are recorded for this topic.', 'review-content-meta');
            return;
        }
        const list = document.createElement('ul');
        items.forEach(item => {
            const entry = document.createElement('li');
            addText(entry, 'strong', item.heading || item.slot_key || item.slot_id);
            addText(
                entry,
                'span',
                ` ${item.current_approved ? 'current' : item.reason} · ${((item.current_source_ids) || []).join(', ') || 'no sources'}`,
                'review-content-meta',
            );
            list.appendChild(entry);
        });
        container.appendChild(list);
    }

    function selectedGenerationCandidate() {
        const slotId = byId('review-generation-slot').value;
        return state.generationCandidates.find(item => item.slotId === slotId) || null;
    }

    function renderGenerationJob() {
        const job = state.contentStatus && state.contentStatus.latest_job;
        if (!job) {
            setGenerationJob('No generation job is recorded for this topic.');
            return;
        }
        const error = job.error ? ` · ${detailText(job.error)}` : '';
        const statusState = ['completed', 'completed_with_errors'].includes(job.status) ? 'success'
            : (['failed', 'interrupted'].includes(job.status) ? 'error' : 'neutral');
        setGenerationJob(
            `Latest job ${job.status} · ${job.job_id} · updated ${job.updated_at || 'not recorded'}${error}`,
            statusState,
        );
    }

    function renderAutomationQueue() {
        const surface = byId('review-automation-queue');
        if (!surface) return;
        const payload = state.automationStatus;
        const jobs = payload && Array.isArray(payload.jobs) ? payload.jobs : [];
        const counts = (payload && payload.counts) || {};
        if (!jobs.length) {
            surface.textContent = 'Automatic queue: no jobs recorded for this topic.';
            surface.dataset.state = 'neutral';
            return;
        }
        const summary = Object.keys(counts).sort().map(key => `${key.replaceAll('_', ' ')} ${counts[key]}`).join(' · ');
        const latest = jobs[0];
        const slot = latest.slot_heading || latest.slot_id;
        surface.textContent = `Automatic queue: ${summary}. Latest ${latest.job_type} ${latest.status.replaceAll('_', ' ')}${slot ? ` · ${slot}` : ''}.`;
        surface.dataset.state = latest.status === 'failed' ? 'error'
            : (latest.status === 'completed' ? 'success' : 'neutral');
    }

    function setGenerationActionState() {
        const candidate = selectedGenerationCandidate();
        const latestJob = state.contentStatus && state.contentStatus.latest_job;
        const jobActive = Boolean(latestJob && ['pending', 'running'].includes(latestJob.status));
        const eligible = Boolean(candidate && candidate.currentApproved);
        byId('review-generate').disabled = !eligible || jobActive;
        byId('review-generation-refresh').disabled = !state.manifest;
        const reason = byId('review-generation-reason').value.trim();
        const actor = byId('review-actor-input').value.trim();
        const approvable = Boolean(
            candidate && !candidate.currentApproved && candidate.currentSourceIds.length && actor && reason,
        );
        byId('review-approve-mapping').disabled = !approvable || jobActive;
        const message = byId('review-generation-eligibility');
        if (!candidate) {
            message.textContent = 'No empty, stale, or failed section is available.';
        } else if (!candidate.currentSourceIds.length) {
            message.textContent = `${candidate.heading} is ${candidate.status}, but no current evidence source is mapped to this slot.`;
        } else if (!candidate.currentApproved) {
            message.textContent = `${candidate.heading} is ${candidate.status}. Review current sources: ${candidate.currentSourceIds.join(', ')}.`;
        } else if (jobActive) {
            message.textContent = `A generation job is already ${latestJob.status}; wait for it to finish before starting another.`;
        } else {
            message.textContent = `${candidate.heading} is ${candidate.status} and eligible for one-section regeneration.`;
        }
    }

    function renderGenerationPanel() {
        const select = byId('review-generation-slot');
        const previous = select.value;
        clear(select);
        const manifest = state.manifest || {};
        const statuses = (state.contentStatus && state.contentStatus.slots) || {};
        const scopeStatuses = new Map(
            (((state.scopePayload && state.scopePayload.all_items) || [])).map(item => [item.slot_id, item]),
        );
        state.generationCandidates = ((manifest.slots) || [])
            .filter(slot => slot.content_target !== false)
            .map(slot => {
                const scope = scopeStatuses.get(slot.slot_id) || {};
                return {
                    slotId: slot.slot_id,
                    heading: slot.heading || slot.slot_id,
                    headingPath: slot.heading_path || [],
                    status: (statuses[slot.slot_id] || {}).status || 'empty',
                    currentApproved: scope.current_approved === true,
                    currentSourceIds: Array.isArray(scope.current_source_ids) ? scope.current_source_ids : [],
                };
            })
            .filter(item => ['empty', 'stale', 'failed'].includes(item.status));

        if (!state.generationCandidates.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No generation candidates';
            select.appendChild(option);
            select.disabled = true;
        } else {
            state.generationCandidates.forEach(item => {
                const option = document.createElement('option');
                option.value = item.slotId;
                const gate = item.currentApproved ? 'mapping approved'
                    : (item.currentSourceIds.length ? 'mapping review required' : 'no mapped evidence');
                option.textContent = `${item.heading} — ${item.status} — ${gate}`;
                select.appendChild(option);
            });
            const preferred = state.generationCandidates.find(item => item.slotId === previous)
                || state.generationCandidates.find(item => item.currentApproved)
                || state.generationCandidates[0];
            select.value = preferred.slotId;
            select.disabled = false;
        }
        renderGenerationJob();
        setGenerationActionState();
    }

    function citationLabel(citation) {
        if (!citation || typeof citation !== 'object') return '';
        return [citation.paper_id, citation.chunk_id].filter(Boolean).join(' · ');
    }

    function renderCitations(parent, citations) {
        if (!Array.isArray(citations) || !citations.length) return;
        const list = document.createElement('ul');
        citations.forEach(citation => {
            const label = citationLabel(citation);
            if (label) addText(list, 'li', label, 'review-content-meta');
        });
        if (list.childElementCount) parent.appendChild(list);
    }

    function renderBlock(parent, block) {
        const section = document.createElement('section');
        section.className = 'review-content-block';
        addText(section, 'h5', block.title || block.type || 'Evidence block');
        if (block.text) addText(section, 'p', block.text);
        if (Array.isArray(block.items)) {
            const list = document.createElement('ul');
            block.items.forEach(item => {
                const entry = document.createElement('li');
                const text = typeof item === 'string' ? item : (item.statement || item.text || item.title || detailText(item));
                addText(entry, 'span', text);
                renderCitations(entry, item && item.citations);
                list.appendChild(entry);
            });
            section.appendChild(list);
        }
        if (Array.isArray(block.columns) && Array.isArray(block.rows)) {
            const table = document.createElement('table');
            const header = document.createElement('tr');
            block.columns.forEach(column => addText(header, 'th', column.label || column.key || column));
            table.appendChild(header);
            block.rows.forEach(row => {
                const tr = document.createElement('tr');
                const cells = Array.isArray(row.cells) ? row.cells : [];
                cells.forEach(cell => addText(tr, 'td', cell.text || cell.value || detailText(cell)));
                table.appendChild(tr);
            });
            section.appendChild(table);
        }
        renderCitations(section, block.citations);
        parent.appendChild(section);
    }

    function renderVersion(containerId, version, emptyMessage) {
        const container = byId(containerId);
        clear(container);
        if (!version || !version.content) {
            addText(container, 'p', emptyMessage, 'review-content-meta');
            return;
        }
        const versionId = version.version_id || version.id;
        addText(
            container,
            'p',
            `${version.workflow_status} · version ${versionId} · updated ${version.updated_at || 'not recorded'}`,
            'review-content-meta',
        );
        const blocks = Array.isArray(version.content.blocks) ? version.content.blocks : [];
        if (!blocks.length) addText(container, 'p', 'No renderable content blocks.', 'review-content-meta');
        blocks.forEach(block => renderBlock(container, block || {}));
    }

    function renderSourceGate(gate) {
        const container = byId('review-source-gate');
        clear(container);
        const allowed = Boolean(gate && gate.allowed);
        addText(
            container,
            'p',
            allowed ? 'Allowed for commercial publication' : 'Blocked for commercial publication',
            allowed ? 'review-gate-allowed' : 'review-gate-blocked',
        );
        addText(container, 'p', `Required use: ${(gate && gate.required_use) || 'not reported'}`, 'review-content-meta');
        addText(container, 'p', `Sources: ${((gate && gate.paper_ids) || []).join(', ') || 'none'}`, 'review-content-meta');
        const blocked = gate && Array.isArray(gate.blocked) ? gate.blocked : [];
        blocked.forEach(item => addText(container, 'p', detailText(item.reason || item.detail || item), 'review-gate-blocked'));
    }

    function renderScopeReview(review) {
        const container = byId('review-scope-review');
        clear(container);
        const current = Boolean(review && review.current_approved);
        addText(
            container,
            'p',
            current ? 'Current evidence mapping approved' : 'Evidence mapping review pending',
            current ? 'review-gate-allowed' : 'review-gate-blocked',
        );
        addText(container, 'p', `Status: ${(review && review.reason) || 'unavailable'}`, 'review-content-meta');
        addText(container, 'p', `Decision: ${(review && review.decision) || 'not recorded'}`, 'review-content-meta');
        addText(
            container,
            'p',
            `Reviewer: ${(review && review.reviewed_by) || 'not recorded'} · ${(review && review.reviewed_at) || 'date unavailable'}`,
            'review-content-meta',
        );
        addText(
            container,
            'p',
            `Approved sources: ${((review && review.approved_source_ids) || []).join(', ') || 'none'}`,
            'review-content-meta',
        );
        addText(
            container,
            'p',
            `Current sources: ${((review && review.current_source_ids) || []).join(', ') || 'none'}`,
            'review-content-meta',
        );
        if (review && review.review_reason) addText(container, 'p', review.review_reason, 'review-content-meta');
    }

    function renderHistory(history, publishedVersion) {
        const container = byId('review-history');
        const target = byId('review-rollback-target');
        clear(container);
        clear(target);
        const emptyOption = document.createElement('option');
        emptyOption.value = '';
        emptyOption.textContent = 'No prior published version';
        target.appendChild(emptyOption);
        const entries = Array.isArray(history) ? history : [];
        if (!entries.length) addText(container, 'p', 'No review decisions recorded.', 'review-content-meta');
        entries.slice().reverse().forEach(item => {
            const article = document.createElement('article');
            article.className = 'review-history-item';
            addText(article, 'strong', `${item.action}: ${item.before_status} → ${item.after_status}`);
            addText(article, 'p', `${item.actor} · ${item.created_at}`, 'review-history-meta');
            if (item.comment) addText(article, 'p', item.comment);
            container.appendChild(article);
        });
        const publishedId = publishedVersion && (publishedVersion.version_id || publishedVersion.id);
        const priorIds = [];
        entries.forEach(item => {
            if ((item.action === 'publish' || item.action === 'rollback') && item.content_version_id !== publishedId) {
                if (!priorIds.includes(item.content_version_id)) priorIds.push(item.content_version_id);
            }
        });
        priorIds.forEach(versionId => {
            const option = document.createElement('option');
            option.value = String(versionId);
            option.textContent = `Version ${versionId}`;
            target.appendChild(option);
        });
        target.disabled = priorIds.length === 0;
    }

    function setActionState() {
        const current = state.detail && state.detail.current;
        const workflow = current && current.workflow_status;
        const gateAllowed = Boolean(state.detail && state.detail.source_gate && state.detail.source_gate.allowed);
        byId('review-submit').disabled = !['generated', 'rejected'].includes(workflow);
        byId('review-approve').disabled = workflow !== 'review_pending';
        byId('review-reject').disabled = workflow !== 'review_pending';
        byId('review-publish').disabled = workflow !== 'approved' || !gateAllowed;
        byId('review-rollback').disabled = !gateAllowed || !byId('review-rollback-target').value;
    }

    function renderDetail(payload) {
        state.detail = payload;
        state.selectedSlotId = payload.slot.slot_id;
        byId('review-detail-title').textContent = payload.slot.heading;
        byId('review-workflow-badge').textContent = payload.current.workflow_status;
        renderVersion('review-draft', payload.current, 'No current draft.');
        renderVersion('review-published', payload.published, 'No published comparison exists.');
        renderScopeReview(payload.scope_review);
        renderSourceGate(payload.source_gate);
        renderHistory(payload.history, payload.published);
        setActionState();
        renderQueue();
    }

    async function loadDetail(slotId) {
        try {
            setStatus('Loading review detail…');
            const topic = topicSlug();
            const payload = await apiJson(
                `/api/v1/topic/${encodeURIComponent(topic)}/content/${encodeURIComponent(slotId)}/review-detail`,
            );
            renderDetail(payload);
            setStatus(`Loaded ${payload.slot.heading}.`, 'success');
        } catch (error) {
            setStatus(error.message, 'error');
        }
    }

    async function loadQueue() {
        try {
            setStatus('Loading medical review queue…');
            const topic = topicSlug();
            const actor = actorName();
            const reviewToken = byId('review-token-input').value.trim();
            if (!reviewToken && !devReviewSessionEnabled()) throw new Error('Review token is required.');
            sessionStorage.setItem('llmebm.review.topic', topic);
            sessionStorage.setItem('llmebm.review.actor', actor);
            const [payload, scopePayload, manifestPayload, statusPayload, automationPayload] = await Promise.all([
                apiJson(`/api/v1/topic/${encodeURIComponent(topic)}/review-queue`),
                apiJson(`/api/v1/topic/${encodeURIComponent(topic)}/scope-reviews`),
                apiJson(`/api/v1/topic/${encodeURIComponent(topic)}/manifest`),
                apiJson(`/api/v1/topic/${encodeURIComponent(topic)}/content/status`),
                apiJson(`/api/v1/topic/${encodeURIComponent(topic)}/automation/status`),
            ]);
            if (reviewToken) saveReviewToken(reviewToken);
            state.items = Array.isArray(payload.items) ? payload.items : [];
            state.selectedSlotId = '';
            state.detail = null;
            state.manifest = manifestPayload && manifestPayload.manifest;
            state.scopePayload = scopePayload;
            state.contentStatus = statusPayload;
            state.automationStatus = automationPayload;
            renderQueue();
            renderScopeReviewSummary(scopePayload);
            renderGenerationPanel();
            renderAutomationQueue();
            setActionState();
            setStatus(`Loaded ${state.items.length} current-ready review item(s).`, 'success');
        } catch (error) {
            state.items = [];
            state.manifest = null;
            state.scopePayload = null;
            state.contentStatus = null;
            state.automationStatus = null;
            state.generationCandidates = [];
            renderQueue();
            renderGenerationPanel();
            renderAutomationQueue();
            setStatus(error.message, 'error');
        }
    }

    async function refreshGenerationStatus() {
        try {
            if (!state.manifest) throw new Error('Load a topic before refreshing generation status.');
            setGenerationJob('Refreshing generation status…');
            const topic = topicSlug();
            [state.contentStatus, state.automationStatus] = await Promise.all([
                apiJson(`/api/v1/topic/${encodeURIComponent(topic)}/content/status`),
                apiJson(`/api/v1/topic/${encodeURIComponent(topic)}/automation/status`),
            ]);
            renderGenerationPanel();
            renderAutomationQueue();
        } catch (error) {
            setGenerationJob(error.message, 'error');
        }
    }

    async function runGeneration() {
        try {
            const candidate = selectedGenerationCandidate();
            if (!candidate) throw new Error('Select a section requiring generation.');
            if (!candidate.currentApproved) {
                throw new Error('Current source-to-slot mapping review is required before generation.');
            }
            if (!window.confirm(`Regenerate only ${candidate.heading}? This action calls the configured LLM pipeline.`)) {
                return;
            }
            const topic = topicSlug();
            setGenerationJob(`Submitting ${candidate.heading}…`);
            const payload = await apiJson(
                `/api/v1/topic/${encodeURIComponent(topic)}/content/generate`,
                {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        only_slot_ids: [candidate.slotId],
                        force: false,
                        require_current_scope_review: true,
                        filters: {},
                        top_k: 10,
                    }),
                },
            );
            setGenerationJob(
                `Generation accepted for ${candidate.heading} · job ${payload.job_id}. Use Refresh job status to follow it.`,
                'success',
            );
            await refreshGenerationStatus();
        } catch (error) {
            setGenerationJob(error.message, 'error');
        }
    }

    async function approveCurrentMapping() {
        try {
            const candidate = selectedGenerationCandidate();
            if (!candidate) throw new Error('Select a section requiring generation.');
            if (!candidate.currentSourceIds.length) throw new Error('The selected section has no current evidence sources.');
            const actor = actorName();
            const reason = byId('review-generation-reason').value.trim();
            if (!reason) throw new Error('A mapping review reason is required.');
            if (!window.confirm(
                `Approve only the current mapping for ${candidate.heading}?\nSources: ${candidate.currentSourceIds.join(', ')}`,
            )) return;
            const topic = topicSlug();
            setGenerationJob(`Recording mapping approval for ${candidate.heading}…`);
            await apiJson(
                `/api/v1/topic/${encodeURIComponent(topic)}/scope-reviews/${encodeURIComponent(candidate.slotId)}/approve-current`,
                {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({actor: actor, comment: reason}),
                },
            );
            byId('review-generation-reason').value = '';
            await loadQueue();
            setGenerationJob(`${candidate.heading} mapping approved against its exact current source revision.`, 'success');
        } catch (error) {
            setGenerationJob(error.message, 'error');
        }
    }

    async function runAction(action) {
        try {
            if (!state.selectedSlotId) throw new Error('Select a review item first.');
            const selectedSlotId = state.selectedSlotId;
            const actor = actorName();
            const comment = byId('review-comment').value.trim();
            if (!comment) throw new Error('A review note is required for every workflow action.');
            const topic = topicSlug();
            const slotPath = `/api/v1/topic/${encodeURIComponent(topic)}/content/${encodeURIComponent(state.selectedSlotId)}`;
            let endpoint = '';
            const body = {actor: actor, comment: comment};
            if (action === 'submit') endpoint = `${slotPath}/review-submit`;
            if (action === 'approve' || action === 'reject') {
                endpoint = `${slotPath}/review-decision`;
                body.decision = action;
            }
            if (action === 'publish') endpoint = `${slotPath}/publish`;
            if (action === 'rollback') {
                endpoint = `${slotPath}/rollback`;
                body.version_id = Number(byId('review-rollback-target').value);
                if (!body.version_id) throw new Error('Select a prior published version.');
            }
            if (!endpoint) throw new Error('Unsupported review action.');
            setStatus(`Applying ${action}…`);
            await apiJson(endpoint, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            byId('review-comment').value = '';
            await loadQueue();
            await loadDetail(selectedSlotId);
            setStatus(`${action} completed and audit history refreshed.`, 'success');
        } catch (error) {
            setStatus(error.message, 'error');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const reviewToken = loadSavedReviewToken();
        const topic = sessionStorage.getItem('llmebm.review.topic');
        const actor = sessionStorage.getItem('llmebm.review.actor');
        if (devReviewSessionEnabled()) {
            byId('review-token-input').value = '';
            try {
                // ponytail: the HttpOnly dev cookie replaces the browser-readable token, so remove any legacy copy.
                localStorage.removeItem(REVIEW_TOKEN_STORAGE_KEY);
            } catch (_error) {
                // Storage denial is harmless because the disabled input remains empty.
            }
        } else if (reviewToken) {
            byId('review-token-input').value = reviewToken;
        }
        if (topic) byId('review-topic-input').value = topic;
        if (actor) byId('review-actor-input').value = actor;
        byId('review-load-queue').addEventListener('click', loadQueue);
        byId('review-status-filter').addEventListener('change', renderQueue);
        byId('review-generation-slot').addEventListener('change', setGenerationActionState);
        byId('review-generation-reason').addEventListener('input', setGenerationActionState);
        byId('review-actor-input').addEventListener('input', setGenerationActionState);
        byId('review-approve-mapping').addEventListener('click', approveCurrentMapping);
        byId('review-generate').addEventListener('click', runGeneration);
        byId('review-generation-refresh').addEventListener('click', refreshGenerationStatus);
        byId('review-rollback-target').addEventListener('change', setActionState);
        byId('review-submit').addEventListener('click', () => runAction('submit'));
        byId('review-approve').addEventListener('click', () => runAction('approve'));
        byId('review-reject').addEventListener('click', () => runAction('reject'));
        byId('review-publish').addEventListener('click', () => runAction('publish'));
        byId('review-rollback').addEventListener('click', () => runAction('rollback'));
    });
}());
