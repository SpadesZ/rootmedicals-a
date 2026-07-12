/*
 * File path: rootmedicals-a/llmebm/app/static/js/topic_content.js
 * Safe deterministic renderer for llmebm-topic-content.v1 component JSON.
 */
(function (global) {
    'use strict';

    const ALLOWED_BLOCKS = new Set([
        'summary', 'recommendations', 'bullets', 'evidence_note', 'table', 'warning'
    ]);

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

    async function selectSlot(button, node) {
        const target = document.getElementById('main-reading-content');
        if (!target) return;
        document.querySelectorAll('[data-slot-id][aria-current="true"]').forEach((element) => {
            element.removeAttribute('aria-current');
        });
        button.setAttribute('aria-current', 'true');
        target.dataset.activeSlotId = node.slot_id;
        target.setAttribute('aria-busy', 'true');
        showState(target, node.name, 'Loading evidence-backed content…', 'topic-content-loading');
        const topicName = document.body.dataset.topicName || '';
        try {
            const response = await fetch(
                `/api/v1/topic/${encodeURIComponent(topicName)}/content/${encodeURIComponent(node.slot_id)}`,
                {headers: {'Accept': 'application/json'}}
            );
            const result = await response.json();
            if (target.dataset.activeSlotId !== node.slot_id) return;
            if (response.status === 404 || result.status === 'empty') {
                showState(target, node.name, 'No generated content is available for this section yet.', 'topic-content-empty');
            } else if (!response.ok) {
                showState(target, node.name, 'Content is temporarily unavailable.', 'topic-content-error');
            } else {
                const heading = document.createElement('h3');
                heading.textContent = node.name;
                target.replaceChildren(heading);
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
        button.addEventListener('click', () => selectSlot(button, node));
    }

    global.TopicContent = Object.freeze({bindSlotButton, renderContent});
})(window);
