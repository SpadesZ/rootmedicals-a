/*
 * 模組定位: llmebm 共用前端互動與首頁產品入口。
 * 主要責任: 同步品牌/字級、控制 About dialog、執行自有 taxonomy 搜尋，並保留 Admin taxonomy/sidebar 編輯行為。
 * 呼叫來源: index.html、specialty.html 與 admin.html 的共用靜態資源。
 * 輸入契約: llmebm-owned settings/search/taxonomy APIs 與可信的使用者 DOM 事件。
 * 輸出契約: About 使用原生 modal dialog；搜尋結果只導向 canonical Topic deep link；未完成入口不得偽裝成可用連結。
 * 安全邊界: 搜尋結果只以 textContent 建立；不得把 query 或 API 文字拼成可執行 HTML。
 * 維護提醒: 共用檔變更須驗 index/topic/admin 三個載入面，並同步 asset cache key。
 * ----------------------------------------------------------------------------------------------------
 */

document.addEventListener("DOMContentLoaded", () => {
    
    // ==========================================
    // 0. 系統品牌狀態同步 (Brand Synchronization)
    // ==========================================
    const sysBrandName = document.getElementById("system-brand-name");
    const sysBrandLogo = document.getElementById("system-brand-logo");

    function loadBrandSettings() {
        fetch('/api/v1/settings')
            .then(res => res.json())
            .then(response => {
                let storedName = "RootMedicals Health";
                let storedLogo = "";
                
                if(response.status === 'success' && response.data.brand_name) {
                    storedName = response.data.brand_name;
                    storedLogo = response.data.brand_logo;
                } else {
                    storedName = localStorage.getItem("rm_brand_name") || "RootMedicals Health";
                    storedLogo = localStorage.getItem("rm_brand_logo");
                }

                if (storedName && sysBrandName) {
                    sysBrandName.textContent = storedName;
                    const mainLogo = document.querySelector(".main-logo");
                    if (mainLogo) {
                        mainLogo.textContent = `${storedName} EBM-Dx`;
                    }
                    const searchInput = document.getElementById("global-search-input");
                    if (searchInput) {
                        searchInput.placeholder = `Search ${storedName} EBM-Dx...`;
                    }
                }

                if (storedLogo && sysBrandLogo) {
                    sysBrandLogo.src = storedLogo;
                    sysBrandLogo.style.display = "inline-block";
                } else if (sysBrandLogo) {
                    sysBrandLogo.style.display = "none";
                }
            })
            .catch(err => {
                console.error("Failed to fetch brand settings:", err);
                const fallbackName = localStorage.getItem("rm_brand_name") || "RootMedicals Health";
                if (sysBrandName) sysBrandName.textContent = fallbackName;
            });
    }

    loadBrandSettings();

    // ==========================================
    // 0.25 共用 About 對話框
    // ==========================================
    const aboutButton = document.getElementById("btn-about");
    const aboutDialog = document.getElementById("about-dialog");
    const aboutCloseButton = document.getElementById("btn-about-close");

    if (aboutButton && aboutDialog && aboutCloseButton) {
        aboutButton.addEventListener("click", () => {
            aboutDialog.showModal();
            aboutButton.setAttribute("aria-expanded", "true");
        });

        aboutCloseButton.addEventListener("click", () => aboutDialog.close());
        aboutDialog.addEventListener("close", () => {
            aboutButton.setAttribute("aria-expanded", "false");
            aboutButton.focus();
        });
    }

    // ==========================================
    // 0.5 Admin 後台設定表單邏輯
    // ==========================================
    const adminForm = document.getElementById("admin-brand-form");
    const brandNameInput = document.getElementById("brand-name-input");
    const logoInput = document.getElementById("brand-logo-input");
    const logoPreview = document.getElementById("logo-preview");
    const btnClearBrand = document.getElementById("btn-clear-brand");
    const statusMsg = document.getElementById("admin-status-msg");

    if (adminForm) {
        fetch('/api/v1/settings').then(res => res.json()).then(resp => {
            if (resp.status === 'success') {
                brandNameInput.value = resp.data.brand_name || localStorage.getItem("rm_brand_name") || "RootMedicals Health";
                const currentLogo = resp.data.brand_logo || localStorage.getItem("rm_brand_logo");
                if (currentLogo) {
                    logoPreview.src = currentLogo;
                    logoPreview.style.display = "inline-block";
                }
            }
        });

        logoInput.addEventListener("change", function(event) {
            const file = event.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    logoPreview.src = e.target.result;
                    logoPreview.style.display = "inline-block";
                };
                reader.readAsDataURL(file);
            }
        });

        adminForm.addEventListener("submit", function(event) {
            event.preventDefault();
            const newBrandName = brandNameInput.value.trim();
            const newLogoStr = (logoPreview.src && logoPreview.src !== window.location.href) ? logoPreview.src : "";
            
            localStorage.setItem("rm_brand_name", newBrandName);
            if (newLogoStr) {
                localStorage.setItem("rm_brand_logo", newLogoStr);
            }

            fetch('/api/v1/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ brand_name: newBrandName, brand_logo: newLogoStr })
            })
            .then(res => res.json())
            .then(data => {
                if(data.status === 'success') {
                    loadBrandSettings(); 
                    statusMsg.textContent = "Settings saved successfully!";
                    statusMsg.classList.remove("hidden");
                    setTimeout(() => { statusMsg.classList.add("hidden"); }, 3000);
                }
            });
        });

        btnClearBrand.addEventListener("click", function() {
            localStorage.removeItem("rm_brand_name");
            localStorage.removeItem("rm_brand_logo");
            
            logoPreview.style.display = "none";
            logoPreview.src = "";
            logoInput.value = "";
            brandNameInput.value = "RootMedicals Health";
            
            fetch('/api/v1/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ brand_name: "RootMedicals Health", brand_logo: "" })
            })
            .then(() => {
                if (sysBrandName) {
                    sysBrandName.textContent = "RootMedicals Health";
                }
                const mainLogo = document.querySelector(".main-logo");
                if (mainLogo) {
                    mainLogo.textContent = "RootMedicals Health EBM-Dx";
                }
                const searchInput = document.getElementById("global-search-input");
                if (searchInput) {
                    searchInput.placeholder = "Search RootMedicals Health EBM-Dx...";
                }
                statusMsg.textContent = "Settings reset to default!";
                statusMsg.classList.remove("hidden");
                setTimeout(() => { statusMsg.classList.add("hidden"); }, 3000);
            });
        });
    }

    // ==========================================
    // 0.8 全域字體縮放管理器 (Font Zooming)
    // ==========================================
    let currentZoom = parseFloat(localStorage.getItem("rm_zoom_level")) || 1.0;
    let currentScale = parseFloat(localStorage.getItem("rm_text_scale")) || 1.15; 
    
    function applyZoom(zoomValue) {
        document.body.style.zoom = zoomValue;
        localStorage.setItem("rm_zoom_level", zoomValue);
    }

    function applyTextScale(scaleValue) {
        document.documentElement.style.setProperty('--text-scale', scaleValue.toString());
        localStorage.setItem("rm_text_scale", scaleValue);
    }

    applyZoom(currentZoom);
    applyTextScale(currentScale);

    const btnIncrease = document.querySelector(".btn-zoom-in") || document.getElementById("btn-font-increase");
    const btnDecrease = document.querySelector(".btn-zoom-out") || document.getElementById("btn-font-decrease");
    const btnReset = document.querySelector(".btn-zoom-reset") || document.getElementById("btn-font-reset");

    if (btnIncrease && btnDecrease && btnReset) {
        btnIncrease.addEventListener("click", () => {
            if (currentZoom < 1.5) { currentZoom += 0.1; applyZoom(currentZoom); }
            if (currentScale < 1.8) { currentScale += 0.1; applyTextScale(currentScale); }
        });
        btnDecrease.addEventListener("click", () => {
            if (currentZoom > 0.8) { currentZoom -= 0.1; applyZoom(currentZoom); }
            if (currentScale > 0.8) { currentScale -= 0.1; applyTextScale(currentScale); }
        });
        btnReset.addEventListener("click", () => {
            currentZoom = 1.0; applyZoom(currentZoom);
            currentScale = 1.15; applyTextScale(currentScale);
        });
    }

    // ==========================================
    // 1. 側邊欄 (Sidebar) 動態渲染邏輯
    // ==========================================
    const conditionChapters = ["General Information", "Epidemiology", "Etiology and Pathogenesis", "History and Physical", "Diagnosis", "Treatment", "Complications and Prognosis", "Prevention and Screening", "Guidelines and Resources", "Patient Information", "ICD-9/ICD-10 Codes", "References"];
    const drugChapters = ["Warnings", "General Information", "Uses and Efficacy", "Dosage and Administration", "Cautions and Adverse Effects", "Interactions", "Mechanism of Action", "Stability and Compatibility", "Preparations", "Patient Information", "Guidelines and Resources", "References"];

    function renderSidebar(type) {
        const sidebarList = document.getElementById("sidebar-chapters");
        if (!sidebarList) return; 
        sidebarList.innerHTML = ""; 
        const chapters = (type === "drug") ? drugChapters : conditionChapters;
        chapters.forEach(chapter => {
            const li = document.createElement("li");
            li.textContent = chapter;
            sidebarList.appendChild(li);
        });
    }
    renderSidebar("condition");

    // ==========================================
    // 2. 全局搜尋 (Global Search)：只查 llmebm-owned topic/heading index
    // ==========================================
    const globalInput = document.getElementById("global-search-input");
    const dropdown = document.getElementById("autocomplete-dropdown");
    const gotoList = document.getElementById("goto-list");
    const searchforList = document.getElementById("searchfor-list");
    const globalSearchButton = document.getElementById("global-search-btn");

    if (globalInput && dropdown && gotoList && searchforList) {
        let searchRequest = 0;
        let currentSearchResults = [];

        function searchResultUrl(row) {
            return `/topic/${encodeURIComponent(row.topic_name)}#slot=${encodeURIComponent(row.slot_id)}`;
        }

        function renderSearchResults(results) {
            gotoList.replaceChildren();
            searchforList.replaceChildren();
            results.forEach((row) => {
                const item = document.createElement("li");
                const button = document.createElement("button");
                button.type = "button";
                button.textContent = `${row.heading} — ${row.topic_name.replaceAll("-", " ")}`;
                button.addEventListener("click", () => { window.location.href = searchResultUrl(row); });
                item.appendChild(button);
                gotoList.appendChild(item);
            });
            const summary = document.createElement("li");
            summary.textContent = results.length
                ? `${results.length} matching llmebm headings. Select one or press Enter for the first result.`
                : "No matching llmebm topic or heading.";
            searchforList.appendChild(summary);
            dropdown.classList.remove("hidden");
        }

        async function runGlobalSearch() {
            const query = globalInput.value.trim();
            const thisRequest = ++searchRequest;
            if (query.length < 2) {
                currentSearchResults = [];
                gotoList.replaceChildren();
                searchforList.replaceChildren();
                dropdown.classList.add("hidden");
                return;
            }
            try {
                const response = await fetch(`/api/v1/search?q=${encodeURIComponent(query)}&limit=8`);
                const payload = await response.json();
                if (thisRequest !== searchRequest) return;
                currentSearchResults = response.ok && Array.isArray(payload.results) ? payload.results : [];
                renderSearchResults(currentSearchResults);
            } catch (error) {
                if (thisRequest !== searchRequest) return;
                currentSearchResults = [];
                gotoList.replaceChildren();
                searchforList.replaceChildren();
                const failure = document.createElement("li");
                failure.textContent = "Search is temporarily unavailable.";
                searchforList.appendChild(failure);
                dropdown.classList.remove("hidden");
            }
        }

        globalInput.addEventListener("input", runGlobalSearch);
        globalInput.addEventListener("keydown", (event) => {
            if (event.key === "Escape") dropdown.classList.add("hidden");
            if (event.key === "Enter" && currentSearchResults.length) {
                event.preventDefault();
                window.location.href = searchResultUrl(currentSearchResults[0]);
            }
        });
        if (globalSearchButton) globalSearchButton.addEventListener("click", () => {
            if (currentSearchResults.length) window.location.href = searchResultUrl(currentSearchResults[0]);
            else runGlobalSearch();
        });
        document.addEventListener("click", (e) => {
            if (!e.target.closest('.search-box-container')) dropdown.classList.add("hidden");
        });
    }

    // ==========================================
    // 3. 頁面內搜尋 (Search Within Text) 邏輯
    // ==========================================
    const localInput = document.getElementById("local-search-input");
    const localControls = document.getElementById("local-search-controls");
    const matchCountSpan = document.getElementById("match-count");
    const btnPrev = document.getElementById("btn-prev-match");
    const btnNext = document.getElementById("btn-next-match");
    const contentArea = document.getElementById("content-area");
    
    if (localInput && contentArea && localControls) {
        let originalHTML = contentArea.innerHTML; 
        let currentMatchIndex = -1;
        let matchElements = [];

        localInput.addEventListener("input", (e) => {
            const keyword = e.target.value.trim();
            contentArea.innerHTML = originalHTML;
            matchElements = [];
            currentMatchIndex = -1;

            if (keyword.length === 0) {
                localControls.classList.add("hidden");
                return;
            }
            const regex = new RegExp(`(${escapeRegExp(keyword)})`, 'gi');
            highlightTextNodes(contentArea, regex);
            matchElements = Array.from(contentArea.querySelectorAll("mark.highlight"));
            if (matchElements.length > 0) {
                localControls.classList.remove("hidden");
                matchCountSpan.textContent = `${matchElements.length} instances found`;
                currentMatchIndex = 0;
                focusMatch(currentMatchIndex);
            } else {
                localControls.classList.remove("hidden");
                matchCountSpan.textContent = `0 instances found`;
            }
        });

        if (btnNext) {
            btnNext.addEventListener("click", () => {
                if (matchElements.length > 0) {
                    currentMatchIndex = (currentMatchIndex + 1) % matchElements.length;
                    focusMatch(currentMatchIndex);
                }
            });
        }
        if (btnPrev) {
            btnPrev.addEventListener("click", () => {
                if (matchElements.length > 0) {
                    currentMatchIndex = (currentMatchIndex - 1 + matchElements.length) % matchElements.length;
                    focusMatch(currentMatchIndex);
                }
            });
        }
        function focusMatch(index) {
            matchElements.forEach(el => el.classList.remove("active"));
            const target = matchElements[index];
            target.classList.add("active");
            target.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        function highlightTextNodes(node, regex) {
            if (node.nodeType === 3) { 
                const match = node.nodeValue.match(regex);
                if (match) {
                    const span = document.createElement('span');
                    span.innerHTML = node.nodeValue.replace(regex, `<mark class="highlight">$1</mark>`);
                    node.parentNode.replaceChild(span, node);
                }
            } else if (node.nodeType === 1 && node.nodeName !== 'SCRIPT' && node.nodeName !== 'STYLE' && node.nodeName !== 'MARK') {
                Array.from(node.childNodes).forEach(child => highlightTextNodes(child, regex));
            }
        }
        function escapeRegExp(string) {
            return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }
    }

    // =========================================================================
    // Admin Tab 切換與 Taxonomy CRUD 邏輯
    // =========================================================================
    
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            
            btn.classList.add('active');
            document.getElementById(btn.getAttribute('data-tab')).classList.add('active');
        });
    });

    const layers = [
        { sel: document.getElementById("select-layer-1"), add: document.getElementById("btn-add-l1"), edit: document.getElementById("btn-edit-l1"), del: document.getElementById("btn-del-l1") },
        { sel: document.getElementById("select-layer-2"), add: document.getElementById("btn-add-l2"), edit: document.getElementById("btn-edit-l2"), del: document.getElementById("btn-del-l2") },
        { sel: document.getElementById("select-layer-3"), add: document.getElementById("btn-add-l3"), edit: document.getElementById("btn-edit-l3"), del: document.getElementById("btn-del-l3") },
        { sel: document.getElementById("select-layer-4"), add: document.getElementById("btn-add-l4"), edit: document.getElementById("btn-edit-l4"), del: document.getElementById("btn-del-l4") },
        { sel: document.getElementById("select-layer-5"), add: document.getElementById("btn-add-l5"), edit: document.getElementById("btn-edit-l5"), del: document.getElementById("btn-del-l5") }
    ];
    const targetInput = document.getElementById("target-parent-id");
    const taxonomyForm = document.getElementById("admin-taxonomy-form");
    const taxStatus = document.getElementById("taxonomy-status-msg");
    const breadcrumbHint = document.getElementById("upload-breadcrumb");

    if (layers[0].sel && targetInput) {
        
        function showTaxMsg(msg, isError=false) {
            taxStatus.textContent = msg;
            taxStatus.className = "alert";
            if (isError) {
                taxStatus.style.background = "#f8d7da"; taxStatus.style.color = "#721c24";
            } else {
                taxStatus.style.background = "#d4edda"; taxStatus.style.color = "#155724";
            }
            taxStatus.classList.remove("hidden");
            setTimeout(() => { taxStatus.classList.add("hidden"); }, 5000);
        }

        function updateUploadBreadcrumb() {
            let parentName = "System Root";
            let nextLayerNum = 1;

            if (layers[4].sel && layers[4].sel.value !== "null") {
                parentName = layers[4].sel.options[layers[4].sel.selectedIndex].text;
                nextLayerNum = 6; 
            } else if (layers[3].sel && layers[3].sel.value !== "null") {
                parentName = layers[3].sel.options[layers[3].sel.selectedIndex].text;
                nextLayerNum = 5;
            } else if (layers[2].sel && layers[2].sel.value !== "null") {
                parentName = layers[2].sel.options[layers[2].sel.selectedIndex].text;
                nextLayerNum = 4;
            } else if (layers[1].sel && layers[1].sel.value !== "null") {
                parentName = layers[1].sel.options[layers[1].sel.selectedIndex].text;
                nextLayerNum = 3;
            } else if (layers[0].sel && layers[0].sel.value !== "null") {
                parentName = layers[0].sel.options[layers[0].sel.selectedIndex].text;
                nextLayerNum = 2;
            }

            if (nextLayerNum > 5) {
                breadcrumbHint.innerHTML = `⚠️ <strong>Layer 5 (Max Depth) Reached.</strong><br>Cannot batch import further sub-nodes here.`;
                breadcrumbHint.style.borderLeftColor = "#dc3545";
            } else {
                breadcrumbHint.innerHTML = `準備建立 <strong>Layer ${nextLayerNum}</strong> 節點<br>所屬父節點：<strong>[${parentName}]</strong>`;
                breadcrumbHint.style.borderLeftColor = "var(--theme-light-teal)";
            }
        }

        function populateDropdown(dropdown, data, defaultText) {
            dropdown.innerHTML = `<option value="null">${defaultText}</option>`;
            data.forEach(node => {
                const opt = document.createElement("option");
                opt.value = node.id;
                opt.textContent = node.name;
                dropdown.appendChild(opt);
            });
            dropdown.disabled = false;
        }

        function resetDropdownsAndBtns(startIndex, message) {
            for (let i = startIndex; i < layers.length; i++) {
                if (layers[i].sel) {
                    layers[i].sel.innerHTML = `<option value="null">${message}</option>`;
                    layers[i].sel.disabled = true;
                    if(layers[i].add) layers[i].add.disabled = true;
                    if(layers[i].edit) layers[i].edit.disabled = true;
                    if(layers[i].del) layers[i].del.disabled = true;
                }
            }
        }

        function updateActionBtns(layerIdx, value) {
            const hasValue = value !== "null";
            if(layers[layerIdx].edit) layers[layerIdx].edit.disabled = !hasValue;
            if(layers[layerIdx].del) layers[layerIdx].del.disabled = !hasValue;
            
            if(layerIdx + 1 < layers.length && layers[layerIdx + 1].add) {
                layers[layerIdx + 1].add.disabled = !hasValue;
            }
        }

        async function fetchAndPopulate(parentId, targetDropdown, defaultText) {
            try {
                const url = parentId === "null" ? '/api/v1/admin/taxonomy' : `/api/v1/admin/taxonomy?parent_id=${parentId}`;
                const res = await fetch(url);
                const json = await res.json();
                if (json.status === 'success') {
                    populateDropdown(targetDropdown, json.data, defaultText);
                }
            } catch (err) { console.error("Failed to fetch taxonomy:", err); }
        }

        fetchAndPopulate("null", layers[0].sel, "-- Select or leave blank for Root --");
        updateUploadBreadcrumb(); 

        layers.forEach((layerObj, idx) => {
            layerObj.sel.addEventListener("change", (e) => {
                const val = e.target.value;
                
                if (val !== "null") {
                    targetInput.value = val;
                } else {
                    targetInput.value = (idx === 0) ? "null" : layers[idx-1].sel.value;
                }
                
                resetDropdownsAndBtns(idx + 1, "-- Waiting for previous layer --");
                updateActionBtns(idx, val);
                updateUploadBreadcrumb();

                if (val !== "null" && idx + 1 < layers.length) {
                    fetchAndPopulate(val, layers[idx+1].sel, `-- Select Layer ${idx+2} Node --`);
                }
            });
        });

        async function apiCrudNode(method, url, bodyObj = null) {
            const options = { method: method, headers: {'Content-Type': 'application/json'} };
            if (bodyObj) options.body = JSON.stringify(bodyObj);
            const res = await fetch(url, options);
            const data = await res.json();
            if (!res.ok || data.status !== 'success') throw new Error(data.detail || "Operation failed");
            return data;
        }

        layers.forEach((layerObj, idx) => {
            if(layerObj.add) {
                layerObj.add.addEventListener('click', async () => {
                    const p_id = (idx === 0) ? "null" : layers[idx-1].sel.value;
                    const nodeName = prompt(`Enter new name for Layer ${idx+1}:\n(You can enter multiple names separated by commas ',')`);
                    if(!nodeName) return;
                    try {
                        const resData = await apiCrudNode('POST', '/api/v1/admin/taxonomy/node', { parent_id: p_id === "null" ? null : parseInt(p_id), name: nodeName });
                        showTaxMsg(resData.message || "Node(s) added successfully!");
                        if(idx === 0) {
                            await fetchAndPopulate("null", layers[0].sel, "-- Select or leave blank for Root --");
                            layers[0].sel.value = resData.node_id;
                            layers[0].sel.dispatchEvent(new Event('change'));
                        } else {
                            await fetchAndPopulate(layers[idx-1].sel.value, layers[idx].sel, `-- Select Layer ${idx+1} Node --`);
                            layers[idx].sel.value = resData.node_id;
                            layers[idx].sel.dispatchEvent(new Event('change'));
                        }
                    } catch(err) { showTaxMsg(err.message, true); }
                });
            }
            if(layerObj.edit) {
                layerObj.edit.addEventListener('click', async () => {
                    const selOpt = layerObj.sel.options[layerObj.sel.selectedIndex];
                    const newName = prompt(`Rename node "${selOpt.text}":`, selOpt.text);
                    if(!newName || newName === selOpt.text) return;
                    try {
                        await apiCrudNode('PUT', `/api/v1/admin/taxonomy/node/${selOpt.value}`, { name: newName });
                        showTaxMsg("Node renamed successfully!");
                        selOpt.text = newName; 
                        updateUploadBreadcrumb(); 
                    } catch(err) { showTaxMsg(err.message, true); }
                });
            }
            if(layerObj.del) {
                layerObj.del.addEventListener('click', async () => {
                    const selOpt = layerObj.sel.options[layerObj.sel.selectedIndex];
                    if(!confirm(`Are you sure you want to delete "${selOpt.text}"? This will be rejected if it has child nodes.`)) return;
                    try {
                        await apiCrudNode('DELETE', `/api/v1/admin/taxonomy/node/${selOpt.value}`);
                        showTaxMsg("Node deleted successfully!");
                        if(idx === 0) fetchAndPopulate("null", layers[0].sel, "-- Select or leave blank for Root --");
                        else layers[idx-1].sel.dispatchEvent(new Event('change'));
                    } catch(err) { showTaxMsg(err.message, true); }
                });
            }
        });

        taxonomyForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const formData = new FormData();
            formData.append("parent_id", targetInput.value);
            formData.append("file", document.getElementById("csv-file-input").files[0]);

            try {
                const res = await fetch('/api/v1/admin/taxonomy/upload', { method: 'POST', body: formData });
                const result = await res.json();
                if (result.status === 'success') {
                    showTaxMsg(result.message);
                    let refreshIdx = 0;
                    if (targetInput.value === "null" || targetInput.value === layers[0].sel.value) refreshIdx = 0;
                    else if (targetInput.value === layers[1].sel.value) refreshIdx = 1;
                    else if (targetInput.value === layers[2].sel.value) refreshIdx = 2;
                    else if (targetInput.value === layers[3].sel.value) refreshIdx = 3;
                    else if (targetInput.value === layers[4].sel.value) refreshIdx = 4;
                    
                    layers[refreshIdx].sel.dispatchEvent(new Event('change'));
                    document.getElementById("csv-file-input").value = ""; 
                } else {
                    throw new Error(result.detail || "Upload failed");
                }
            } catch (err) { showTaxMsg(err.message, true); }
        });
    }

    // =========================================================================
    // [v1.5 全新擴充] Sidebar Topic Builder 雙畫布邏輯
    // =========================================================================
    const sbSpecialtySelect = document.getElementById("sb-specialty-select");
    const sbTaxonomyTree = document.getElementById("sb-taxonomy-tree");
    const sbCurrentTopicSpan = document.getElementById("sb-current-topic");
    const sbUniversalTree = document.getElementById("sb-universal-tree");
    const sbCustomTree = document.getElementById("sb-custom-tree");
    
    const sbParentSelect = document.getElementById("sb-parent-select");
    const sbNodeNameInput = document.getElementById("sb-node-name");
    const sbBtnAdd = document.getElementById("sb-btn-add");
    const sbStatusMsg = document.getElementById("sidebar-status-msg");
    
    let activeTopic = null;

    if (sbSpecialtySelect) {
        
        function showSbMsg(msg, isError=false) {
            sbStatusMsg.textContent = msg;
            sbStatusMsg.className = "alert";
            if (isError) { sbStatusMsg.style.background = "#f8d7da"; sbStatusMsg.style.color = "#721c24"; }
            else { sbStatusMsg.style.background = "#d4edda"; sbStatusMsg.style.color = "#155724"; }
            sbStatusMsg.classList.remove("hidden");
            setTimeout(() => { sbStatusMsg.classList.add("hidden"); }, 3000);
        }

        // 載入左側 L1 下拉選單
        fetch('/api/v1/specialties').then(r=>r.json()).then(json => {
            if(json.status === 'success') {
                json.data.forEach(sp => {
                    const opt = document.createElement('option');
                    opt.value = sp.name; opt.textContent = sp.name;
                    sbSpecialtySelect.appendChild(opt);
                });
            }
        });

        // 渲染左側 5層樹狀，點擊葉節點時啟動右側
        function renderTaxonomyTreeForSb(nodes, container) {
            const ul = document.createElement('ul');
            ul.style.listStyleType = "none"; ul.style.paddingLeft = "15px";
            nodes.forEach(node => {
                const li = document.createElement('li');
                li.style.marginTop = "5px";
                const isGroup = node.children && node.children.length > 0;
                
                if (isGroup) {
                    li.innerHTML = `<strong>${node.name}</strong>`;
                    renderTaxonomyTreeForSb(node.children, li);
                } else {
                    const a = document.createElement('a');
                    a.href = "#"; a.textContent = node.name;
                    a.style.color = "#006e82"; a.style.textDecoration = "none";
                    a.addEventListener('click', (e) => {
                        e.preventDefault();
                        activeTopic = node.name;
                        sbCurrentTopicSpan.textContent = activeTopic;
                        loadSidebarEditor();
                    });
                    li.appendChild(a);
                }
                ul.appendChild(li);
            });
            container.appendChild(ul);
        }

        sbSpecialtySelect.addEventListener('change', async (e) => {
            const spName = e.target.value;
            if(!spName) { sbTaxonomyTree.innerHTML = "Select a specialty above."; return; }
            sbTaxonomyTree.innerHTML = "Loading...";
            const res = await fetch(`/api/v1/specialties/${encodeURIComponent(spName)}/tree`);
            const json = await res.json();
            sbTaxonomyTree.innerHTML = "";
            if(json.tree && json.tree.length > 0) renderTaxonomyTreeForSb(json.tree, sbTaxonomyTree);
            else sbTaxonomyTree.innerHTML = "No detailed pathways.";
        });

        // 渲染右側樹狀結構 (通用渲染器)
        function renderSbTreeHTML(nodes, isCustom) {
            if(!nodes || nodes.length === 0) return `<div style="color:#aaa;">No nodes configured.</div>`;
            let html = `<ul style="list-style-type:none; padding-left:0; margin:0;">`;
            nodes.forEach(node => {
                html += `<li style="margin-top:8px; padding-left:${(node.layer-1)*15}px;">`;
                if(node.layer === 1) html += `<strong>${node.name}</strong>`;
                else html += `• ${node.name}`;
                
                if(isCustom) {
                    html += ` <button class="btn-del-sb-node" data-id="${node.id}" style="border:none; background:none; color:red; cursor:pointer; font-size:10px;">[x]</button>`;
                }
                if(node.children && node.children.length > 0) html += renderSbTreeHTML(node.children, isCustom);
                html += `</li>`;
            });
            html += `</ul>`;
            return html;
        }

        // 載入右側畫布資料
        async function loadSidebarEditor() {
            if(!activeTopic) return;
            sbParentSelect.disabled = false; sbNodeNameInput.disabled = false; sbBtnAdd.disabled = false;
            
            try {
                // 1. 載入 Universal 與 Custom 樹狀圖
                const res = await fetch(`/api/v1/topic/${encodeURIComponent(activeTopic)}/sidebar`);
                const json = await res.json();
                sbUniversalTree.innerHTML = renderSbTreeHTML(json.tree.universal, false);
                sbCustomTree.innerHTML = renderSbTreeHTML(json.tree.custom, true);
                
                // 綁定 Custom Tree 的刪除按鈕
                document.querySelectorAll('.btn-del-sb-node').forEach(btn => {
                    btn.addEventListener('click', async (e) => {
                        const nid = e.target.getAttribute('data-id');
                        if(confirm("Delete this custom node?")) {
                            const dRes = await fetch(`/api/v1/topic/sidebar/node/${nid}`, {method: 'DELETE'});
                            if(dRes.ok) { showSbMsg("Deleted."); loadSidebarEditor(); }
                            else { const dJson = await dRes.json(); showSbMsg(dJson.detail || "Error", true); }
                        }
                    });
                });

                // 2. 載入下拉選單 (供新增節點選父層)
                const resFlat = await fetch(`/api/v1/topic/${encodeURIComponent(activeTopic)}/sidebar/flat`);
                const flatJson = await resFlat.json();
                sbParentSelect.innerHTML = `<option value="null">-- Add as Layer 1 (Specialized Feature) --</option>`;
                flatJson.data.forEach(n => {
                    const opt = document.createElement('option');
                    opt.value = n.id;
                    opt.textContent = `[Layer ${n.layer_level}] ${n.name}`;
                    sbParentSelect.appendChild(opt);
                });

            } catch (err) { showSbMsg("Failed to load topic sidebar data.", true); }
        }

        // 新增 Custom Node API
        sbBtnAdd.addEventListener('click', async () => {
            const name = sbNodeNameInput.value.trim();
            if(!name) return;
            const pid = sbParentSelect.value === "null" ? null : parseInt(sbParentSelect.value);
            try {
                const res = await fetch(`/api/v1/topic/${encodeURIComponent(activeTopic)}/sidebar/node`, {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({ parent_id: pid, name: name })
                });
                if(res.ok) {
                    showSbMsg("Added specialized feature.");
                    sbNodeNameInput.value = "";
                    loadSidebarEditor();
                } else {
                    const d = await res.json(); showSbMsg(d.detail, true);
                }
            } catch(err) { showSbMsg(err.message, true); }
        });
    }
});
