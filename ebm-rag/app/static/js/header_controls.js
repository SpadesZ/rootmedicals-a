/*
 * 檔案路徑: rootmedicals-a/ebm-rag/app/static/js/header_controls.js
 * 產生時間: 2026-06-17 16:10 +08:00
 * 版本: v0.1-交付整理
 * 說明: RAG 管理介面前端靜態資源。
 * 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
 * ----------------------------------------------------------------------------------------------------
 */

/*
File Path: ebm-rag/app/static/js/header_controls.js
Timestamp: 2026-06-11
Version: v0.1
Description: Shared header controls for local documentation, UI scale, and browser-local branding.
----------------------------------------------------------------------------------------------------
*/

(function () {
    'use strict';

    const STORAGE_KEYS = {
        brandName: 'rootmedicals.brand.name',
        brandLogo: 'rootmedicals.brand.logoDataUrl',
        uiScale: 'rootmedicals.ui.scale'
    };

    const DEFAULT_BRAND_NAME = 'Rootmedicals-EBMRAG Center';
    const DEFAULT_SCALE = 1;
    const MIN_SCALE = 0.85;
    const MAX_SCALE = 1.25;
    const SCALE_STEP = 0.05;

    const readStorage = (key) => {
        try {
            return window.localStorage.getItem(key);
        } catch (error) {
            return null;
        }
    };

    const writeStorage = (key, value) => {
        try {
            window.localStorage.setItem(key, value);
            return true;
        } catch (error) {
            return false;
        }
    };

    const clampScale = (value) => {
        const numericValue = Number(value);
        if (Number.isFinite(numericValue) === false) {
            return DEFAULT_SCALE;
        }
        return Math.min(MAX_SCALE, Math.max(MIN_SCALE, numericValue));
    };

    const getStoredScale = () => {
        const storedValue = readStorage(STORAGE_KEYS.uiScale);
        if (storedValue === null || storedValue === '') {
            return DEFAULT_SCALE;
        }
        return clampScale(storedValue);
    };

    const updateFontButtonStates = (scale) => {
        const decreaseButton = document.getElementById('btn-font-decrease');
        const resetButton = document.getElementById('btn-font-reset');
        const increaseButton = document.getElementById('btn-font-increase');

        if (decreaseButton !== null) {
            decreaseButton.disabled = scale <= MIN_SCALE;
            decreaseButton.setAttribute('aria-label', `Decrease UI scale. Current scale ${Math.round(scale * 100)} percent.`);
        }
        if (resetButton !== null) {
            resetButton.disabled = Math.abs(scale - DEFAULT_SCALE) < 0.001;
            resetButton.setAttribute('aria-label', `Reset UI scale. Current scale ${Math.round(scale * 100)} percent.`);
        }
        if (increaseButton !== null) {
            increaseButton.disabled = scale >= MAX_SCALE;
            increaseButton.setAttribute('aria-label', `Increase UI scale. Current scale ${Math.round(scale * 100)} percent.`);
        }
    };

    const applyScale = (scale) => {
        const safeScale = clampScale(scale);
        document.documentElement.style.setProperty('--rootmedicals-ui-scale', String(safeScale));
        document.body.style.zoom = String(safeScale);
        writeStorage(STORAGE_KEYS.uiScale, String(safeScale));
        updateFontButtonStates(safeScale);
        return safeScale;
    };

    const attachFontControls = () => {
        const decreaseButton = document.getElementById('btn-font-decrease');
        const resetButton = document.getElementById('btn-font-reset');
        const increaseButton = document.getElementById('btn-font-increase');

        if (decreaseButton !== null) {
            decreaseButton.addEventListener('click', () => {
                applyScale(getStoredScale() - SCALE_STEP);
            });
        }
        if (resetButton !== null) {
            resetButton.addEventListener('click', () => {
                applyScale(DEFAULT_SCALE);
            });
        }
        if (increaseButton !== null) {
            increaseButton.addEventListener('click', () => {
                applyScale(getStoredScale() + SCALE_STEP);
            });
        }
    };

    const applyStoredBrand = () => {
        const storedName = readStorage(STORAGE_KEYS.brandName);
        const brandName = storedName !== null && storedName.trim() !== '' ? storedName.trim() : DEFAULT_BRAND_NAME;
        const storedLogo = readStorage(STORAGE_KEYS.brandLogo);

        document.querySelectorAll('#system-brand-name, .brand-name').forEach((element) => {
            element.textContent = brandName;
        });

        document.querySelectorAll('#system-brand-logo').forEach((imageElement) => {
            if (storedLogo !== null && storedLogo.trim() !== '') {
                imageElement.src = storedLogo;
                imageElement.style.display = 'inline-block';
            } else {
                imageElement.removeAttribute('src');
                imageElement.style.display = 'none';
            }
        });
    };

    const closeDocumentationPanel = () => {
        const overlay = document.getElementById('rootmedicals-doc-panel-overlay');
        if (overlay !== null) {
            overlay.remove();
        }
    };

    const createDocumentationPanel = () => {
        const overlay = document.createElement('div');
        overlay.id = 'rootmedicals-doc-panel-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'RootMedicals demo documentation');
        overlay.style.position = 'fixed';
        overlay.style.inset = '0';
        overlay.style.background = 'rgba(17, 24, 39, 0.58)';
        overlay.style.display = 'flex';
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';
        overlay.style.zIndex = '3000';
        overlay.style.padding = '24px';

        const panel = document.createElement('div');
        panel.style.width = 'min(620px, 100%)';
        panel.style.background = '#ffffff';
        panel.style.borderRadius = '8px';
        panel.style.boxShadow = '0 20px 35px rgba(0,0,0,0.25)';
        panel.style.padding = '24px';
        panel.style.color = '#1f2937';
        panel.style.fontFamily = "'Inter', -apple-system, sans-serif";

        const title = document.createElement('h2');
        title.textContent = 'RootMedicals Demo Quick Guide';
        title.style.margin = '0 0 12px';
        title.style.color = '#005b6e';
        title.style.fontSize = '18px';

        const guideList = document.createElement('ul');
        guideList.style.margin = '0';
        guideList.style.padding = '0 0 0 18px';
        guideList.style.lineHeight = '1.7';
        guideList.style.fontSize = '13px';
        [
            'Main page uploads a PDF and shows extraction progress.',
            'Admin Viewer opens processed papers and supports safe purge confirmation.',
            'RAG Operations checks readiness, indexes selected papers, and runs Query Tester.',
            'Demo-only synthetic fallback is controlled inside Query Tester and remains gated by verifier scoring.',
            'LAVA Setup manages provider connections and task bindings.'
        ].forEach((itemText) => {
            const item = document.createElement('li');
            item.textContent = itemText;
            guideList.appendChild(item);
        });

        const actions = document.createElement('div');
        actions.style.display = 'flex';
        actions.style.gap = '10px';
        actions.style.justifyContent = 'flex-end';
        actions.style.marginTop = '20px';

        const adminButton = document.createElement('button');
        adminButton.type = 'button';
        adminButton.textContent = 'Open Admin';
        adminButton.style.background = '#005b6e';
        adminButton.style.color = '#ffffff';
        adminButton.style.border = 'none';
        adminButton.style.borderRadius = '6px';
        adminButton.style.padding = '9px 14px';
        adminButton.style.cursor = 'pointer';
        adminButton.addEventListener('click', () => {
            window.location.href = '/admin';
        });

        const lavaButton = document.createElement('button');
        lavaButton.type = 'button';
        lavaButton.textContent = 'Open LAVA';
        lavaButton.style.background = '#374151';
        lavaButton.style.color = '#ffffff';
        lavaButton.style.border = 'none';
        lavaButton.style.borderRadius = '6px';
        lavaButton.style.padding = '9px 14px';
        lavaButton.style.cursor = 'pointer';
        lavaButton.addEventListener('click', () => {
            window.location.href = '/lava';
        });

        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.textContent = 'Close';
        closeButton.style.background = '#ffffff';
        closeButton.style.color = '#374151';
        closeButton.style.border = '1px solid #d1d5db';
        closeButton.style.borderRadius = '6px';
        closeButton.style.padding = '9px 14px';
        closeButton.style.cursor = 'pointer';
        closeButton.addEventListener('click', closeDocumentationPanel);

        actions.appendChild(closeButton);
        actions.appendChild(lavaButton);
        actions.appendChild(adminButton);
        panel.appendChild(title);
        panel.appendChild(guideList);
        panel.appendChild(actions);
        overlay.appendChild(panel);

        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) {
                closeDocumentationPanel();
            }
        });

        return overlay;
    };

    const openDocumentationPanel = () => {
        closeDocumentationPanel();
        document.body.appendChild(createDocumentationPanel());
    };

    const attachDocumentationControl = () => {
        const documentationButton = document.getElementById('btn-open-documentation');
        if (documentationButton === null) {
            return;
        }
        documentationButton.addEventListener('click', openDocumentationPanel);
        documentationButton.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openDocumentationPanel();
            }
        });
    };

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeDocumentationPanel();
        }
    });

    document.addEventListener('DOMContentLoaded', () => {
        applyScale(getStoredScale());
        attachFontControls();
        attachDocumentationControl();
        applyStoredBrand();
    });

    window.RootmedicalsHeaderControls = {
        applyScale,
        applyStoredBrand,
        closeDocumentationPanel,
        openDocumentationPanel
    };

    window.addEventListener('rootmedicals:brand-updated', applyStoredBrand);
}());
