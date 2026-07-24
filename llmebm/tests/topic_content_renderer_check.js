/*
 * 模組定位: llmebm Topic renderer 與閱讀導覽的零依賴 self-check。
 * 主要責任: 執行實際 renderer/tabs/back-to-top，驗證安全 DOM 與鍵盤契約。
 * 呼叫來源: Phase 驗收命令與開發者本機 Node 檢查。
 * 輸入契約: topic_content.js 與固定 llmebm-topic-content.v1 攻擊字串 fixture。
 * 輸出契約: 成功印出 PASS；不安全 DOM、假 tab 或失效回頂時立即非零退出。
 * 安全邊界: 不開網路、不讀 runtime DB，只使用 Node standard library 與 fake DOM。
 * 維護提醒: renderer 使用新的 DOM API 或 block type 時，只補足最小 fake DOM contract。
 * ----------------------------------------------------------------------------------------------------
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class FakeElement {
    constructor(tagName) {
        this.tagName = String(tagName).toUpperCase();
        this.childNodes = [];
        this.attributes = {};
        this.className = '';
        this.textContent = '';
        this.dataset = {};
        this.hidden = false;
        this.id = '';
        this.tabIndex = 0;
        this.scrollTop = 0;
        this.focused = false;
        this.listeners = {};
        const classes = new Set();
        this.classList = {
            add: (...names) => names.forEach((name) => classes.add(name)),
            remove: (...names) => names.forEach((name) => classes.delete(name)),
            contains: (name) => classes.has(name),
            toggle: (name, force) => {
                const enabled = force === undefined ? !classes.has(name) : Boolean(force);
                if (enabled) classes.add(name); else classes.delete(name);
                return enabled;
            },
        };
    }

    appendChild(child) {
        this.childNodes.push(child);
        return child;
    }

    replaceChildren(...children) {
        this.childNodes = [...children];
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return this.attributes[name] ?? null;
    }

    addEventListener(type, listener) {
        (this.listeners[type] ||= []).push(listener);
    }

    dispatch(type, event = {}) {
        this.listeners[type]?.forEach((listener) => listener(event));
    }

    focus() {
        this.focused = true;
    }

    scrollTo(options) {
        this.lastScroll = options;
        this.scrollTop = options.top;
    }

    get lastElementChild() {
        return this.childNodes[this.childNodes.length - 1] || null;
    }
}

function walk(element, result = []) {
    result.push(element);
    element.childNodes.forEach((child) => walk(child, result));
    return result;
}

const rendererPath = path.join(__dirname, '..', 'app', 'static', 'js', 'topic_content.js');
const window = {};
const elementsById = {};
const document = {
    createElement: (tagName) => new FakeElement(tagName),
    getElementById: (id) => elementsById[id] || null,
};
vm.runInNewContext(fs.readFileSync(rendererPath, 'utf8'), {window, document});

const attack = '<img src=x onerror="globalThis.pwned=true"><script>globalThis.pwned=true</script>';
const target = new FakeElement('div');
window.TopicContent.renderContent({
    schema: 'llmebm-topic-content.v1',
    status: 'ready',
    blocks: [{type: 'summary', text: attack, citations: []}],
}, target);

const nodes = walk(target);
assert.equal(nodes.some((node) => node.tagName === 'SCRIPT' || node.tagName === 'IMG'), false);
assert.equal(nodes.some((node) => node.textContent === attack), true);
assert.equal(globalThis.pwned, undefined);

const topicTab = new FakeElement('button');
topicTab.id = 'topic-tab';
topicTab.setAttribute('aria-controls', 'topic-panel');
const updatesTab = new FakeElement('button');
updatesTab.id = 'updates-tab';
updatesTab.setAttribute('aria-controls', 'updates-panel');
const topicPanel = new FakeElement('section');
topicPanel.id = 'topic-panel';
const updatesPanel = new FakeElement('section');
updatesPanel.id = 'updates-panel';

window.TopicContent.bindTopicTabs([topicTab, updatesTab], [topicPanel, updatesPanel]);
updatesTab.dispatch('click');
assert.equal(topicTab.getAttribute('aria-selected'), 'false');
assert.equal(updatesTab.getAttribute('aria-selected'), 'true');
assert.equal(topicPanel.hidden, true);
assert.equal(updatesPanel.hidden, false);

let prevented = false;
updatesTab.dispatch('keydown', {key: 'ArrowLeft', preventDefault: () => { prevented = true; }});
assert.equal(prevented, true);
assert.equal(topicTab.getAttribute('aria-selected'), 'true');
assert.equal(topicTab.focused, true);
topicTab.dispatch('keydown', {key: 'End', preventDefault: () => {}});
assert.equal(updatesTab.getAttribute('aria-selected'), 'true');
updatesTab.dispatch('keydown', {key: 'Home', preventDefault: () => {}});
assert.equal(topicTab.getAttribute('aria-selected'), 'true');

const updatesContent = new FakeElement('div');
elementsById['topic-updates-content'] = updatesContent;
window.TopicContent.renderUpdateStatus(
    {name: 'Evaluation', slot_id: 'T:universal:u1', content_status: 'stale'},
    {status: 'ready', updated_at: '2026-07-14T01:02:03Z'}
);
assert.equal(updatesContent.dataset.contentStatus, 'stale');
assert.equal(walk(updatesContent).some((node) => /evidence scope changed/.test(node.textContent)), true);
assert.equal(walk(updatesContent).some((node) => /2026-07-14 01:02 UTC/.test(node.textContent)), true);

const trustHeader = new FakeElement('section');
elementsById['topic-trust-header'] = trustHeader;
window.TopicContent.renderTrustHeader(
    {name: 'Management', slot_id: 'T:universal:u4', content_status: 'ready'},
    {
        status: 'ready', visibility: 'draft_preview', workflow_status: 'review_pending',
        version_id: 100, updated_at: '2026-07-17T10:31:27Z', reviewed_by: null,
    }
);
assert.equal(trustHeader.dataset.workflowStatus, 'review_pending');
assert.equal(trustHeader.dataset.visibility, 'draft_preview');
assert.equal(walk(trustHeader).some((node) => /Demo preview/.test(node.textContent)), true);
assert.equal(walk(trustHeader).some((node) => /Version 100/.test(node.textContent)), true);
assert.equal(walk(trustHeader).some((node) => /Clinical review pending/.test(node.textContent)), true);

const pane = new FakeElement('main');
const backToTop = new FakeElement('button');
window.TopicContent.bindBackToTop(pane, backToTop, 320);
assert.equal(backToTop.hidden, true);
pane.scrollTop = 400;
pane.dispatch('scroll');
assert.equal(backToTop.hidden, false);
backToTop.dispatch('click');
assert.equal(pane.lastScroll.top, 0);
assert.equal(pane.lastScroll.behavior, 'smooth');

console.log('PASS: renderer remained inert and Topic navigation contracts held');
