/*
 * 檔案路徑: rootmedicals-a/ebm-rag/app/static/js/lava_setup.js
 * 產生時間: 2026-06-17 16:10 +08:00
 * 版本: v0.1-交付整理
 * 說明: RAG 管理介面前端靜態資源。
 * 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
 * ----------------------------------------------------------------------------------------------------
 */

/*
File Path: ebm-rag/app/static/js/lava_setup.js
Timestamp: 2026-06-08
Version: v0.7
Description: LAVA Setup UI script. Shows capability-verified connection state and blocks mismatched task binding.
----------------------------------------------------------------------------------------------------
*/

let _connections = [];
let _tasks = [];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  }[char]));
}

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  let payload = {};
  try {
    payload = await r.json();
  } catch (error) {
    payload = { error: "Response is not valid JSON" };
  }
  if (!r.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    return { ok: false, detail, error: payload.error || detail };
  }
  return payload;
}

function setStatus(id, msg, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `status-msg ${ok ? 'status-ok' : 'status-err'}`;
  el.textContent = msg;
}

async function fetchModels() {
  const provider = document.getElementById("new-provider").value;
  const api_key = document.getElementById("new-api-key").value.trim();
  if (!api_key) { setStatus("add-status", "API key required", false); return; }
  setStatus("add-status", "Fetching models", true);
  const data = await api("POST", "/api/lava/models/fetch", { provider, api_key });
  if (data.ok) {
    const sel = document.getElementById("new-model-id");
    sel.innerHTML = data.models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
    document.getElementById("models-container").style.display = "block";
    setStatus("add-status", `${data.models.length} models loaded`, true);
  } else {
    setStatus("add-status", data.error || "Failed", false);
  }
}

async function testConnection(capability = "chat") {
  const provider = document.getElementById("new-provider").value;
  const api_key = document.getElementById("new-api-key").value.trim();
  const model_id = document.getElementById("new-model-id").value;
  if (!model_id) { setStatus("add-status", "Select a model first", false); return; }
  setStatus("add-status", `Testing ${capability} connection`, true);
  // Create temp connection for verify
  const conn = await api("POST", "/api/lava/connections", {
    provider, api_key, model_id, name: "_test_"
  });
  if (!conn.id) { setStatus("add-status", conn.detail || "Failed to create", false); return; }
  const v = await api("POST", "/api/lava/connections/verify", { connection_id: conn.id, capability });
  // Delete test connection
  await api("DELETE", `/api/lava/connections/${conn.id}`);
  if (v.ok) {
    const detail = capability === "embedding" ? `dim=${v.dim || 0}` : `sample="${(v.sample || "").substring(0, 50)}"`;
    setStatus("add-status", `Test OK — ${detail}`, true);
  } else {
    setStatus("add-status", `Test failed: ${v.error}`, false);
  }
}

async function createConnection() {
  const provider = document.getElementById("new-provider").value;
  const api_key = document.getElementById("new-api-key").value.trim();
  const model_id = document.getElementById("new-model-id").value;
  const name = document.getElementById("new-name").value.trim() || provider;
  if (!model_id || !api_key) { setStatus("add-status", "API key and model required", false); return; }
  const conn = await api("POST", "/api/lava/connections", { provider, api_key, model_id, name });
  if (conn.id) {
    setStatus("add-status", `Connection created (ID: ${conn.id})`, true);
    loadConnections();
  } else {
    setStatus("add-status", conn.detail || "Error", false);
  }
}

async function loadConnections() {
  _connections = await api("GET", "/api/lava/connections");
  const tbody = document.getElementById("conn-list");
  if (!Array.isArray(_connections) || _connections.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:#9ca3af;text-align:center">No connections</td></tr>`;
    loadBindings();
    return;
  }
  tbody.innerHTML = _connections.map(c => `
    <tr>
      <td>${escapeHtml(c.id)}</td>
      <td>${escapeHtml(c.name || "-")}</td>
      <td><span class="tag tag-green">${escapeHtml(c.provider)}</span></td>
      <td><code>${escapeHtml(c.model_id)}</code></td>
      <td>
        <span class="tag ${c.is_active ? 'tag-green' : 'tag-yellow'}">${c.is_active ? 'Active' : 'Inactive'}</span>
        <span class="tag ${c.verify_status === 'ok' ? 'tag-green' : 'tag-yellow'}">${escapeHtml(c.verify_status || 'unverified')}</span>
        <span class="tag ${c.verified_capability ? 'tag-green' : 'tag-yellow'}">${escapeHtml(c.verified_capability || 'no capability')}</span>
        ${c.last_error ? `<div style="color:#92400e;font-size:11px;margin-top:4px;max-width:260px;white-space:normal">${escapeHtml(c.last_error)}</div>` : ''}
      </td>
      <td>
        <button class="btn-sm btn-success" onclick="verifyConn(${c.id}, 'chat')">Verify Chat</button>
        <button class="btn-sm btn-success" onclick="verifyConn(${c.id}, 'embedding')">Verify Embedding</button>
        <button class="btn-sm btn-danger" onclick="deleteConn(${c.id})">Del</button>
      </td>
    </tr>`).join("");
  loadBindings();
}

async function verifyConn(id, capability) {
  setStatus("connection-status", `Verifying connection ${id} for ${capability}`, true);
  const result = await api("POST", "/api/lava/connections/verify", { connection_id: id, capability });
  if (result.ok) {
    const detail = capability === "embedding" ? `dim=${result.dim || 0}` : `sample="${(result.sample || "").substring(0, 50)}"`;
    setStatus("connection-status", `Verification OK for connection ${id}: ${detail}`, true);
  } else {
    const preservedText = result.preserved_existing_verification ? " Existing verified state was preserved." : "";
    setStatus("connection-status", `Verification failed for connection ${id}: ${result.error || result.detail || "Verification failed"}.${preservedText}`, false);
  }
  loadConnections();
}

async function deleteConn(id) {
  if (!confirm("Delete connection?")) return;
  await api("DELETE", `/api/lava/connections/${id}`);
  loadConnections();
}

async function loadBindings() {
  _tasks = await api("GET", "/api/lava/tasks");
  const bindings = await api("GET", "/api/lava/bindings");
  const bindingMap = {};
  if (Array.isArray(bindings)) {
    bindings.forEach(b => { bindingMap[b.task_id] = b.connection_id; });
  }
  const container = document.getElementById("bindings-container");
  if (!Array.isArray(_tasks)) { container.innerHTML = "Failed to load tasks"; return; }
  container.innerHTML = _tasks.map(t => {
    const bound = bindingMap[t.task_id];
    const activeOptions = (_connections || [])
      .filter(c => c.is_active && c.verify_status === "ok" && c.verified_capability === t.capability)
      .map(c => ({
        id: String(c.id),
        html: `<option value="${escapeHtml(c.id)}" ${bound == c.id ? "selected" : ""}>${escapeHtml(c.name || c.provider)} — ${escapeHtml(c.model_id)}</option>`
      }));
    const connOptions = activeOptions.map(option => option.html).join("");
    const hasSelectedActiveOption = activeOptions.some(option => option.id === String(bound));
    const boundConn = (_connections || []).find(c => String(c.id) === String(bound));
    const staleBoundOption = bound && boundConn && !hasSelectedActiveOption
      ? `<option value="${escapeHtml(boundConn.id)}" selected disabled>Bound: ${escapeHtml(boundConn.name || boundConn.provider)} — ${escapeHtml(boundConn.model_id)} (not ready)</option>`
      : "";
    const unboundSelected = bound ? "" : "selected";
    const requiredHint = t.required ? '<span class="tag tag-red" style="margin-left:6px">required</span>' : '';
    const noReadyHint = connOptions === "" && staleBoundOption === "" ? '<div style="color:#991b1b;font-size:11px;margin-top:4px">No verified connection is available for this task capability.</div>' : '';
    const staleHint = staleBoundOption ? '<div style="color:#92400e;font-size:11px;margin-top:4px">Existing binding is preserved, but the connection is not currently ready.</div>' : '';
    const selectDisabled = t.implemented ? "" : "disabled";
    const buttonDisabled = t.implemented ? "" : "disabled";
    const buttonTitle = t.implemented ? "" : 'title="Task module is not implemented"';
    return `<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #f3f4f6">
      <div style="flex:1">
        <strong>${escapeHtml(t.label)}</strong>
        <span class="tag ${t.capability === 'embedding' ? 'tag-yellow' : 'tag-green'}" style="margin-left:6px">${t.capability}</span>
        ${requiredHint}
        <div style="color:#6b7280;font-size:11px;margin-top:2px">${escapeHtml(t.description)}</div>
        ${t.implemented ? '' : '<span class="tag tag-red">not implemented</span>'}
        ${noReadyHint}
        ${staleHint}
      </div>
      <select id="bind-${t.task_id}" style="width:260px;margin:0" ${selectDisabled}>
        <option value="" ${unboundSelected}>— unbound —</option>
        ${staleBoundOption}
        ${connOptions}
      </select>
      <button class="btn-sm btn-primary" onclick="bindTask('${t.task_id}')" ${buttonDisabled} ${buttonTitle}>Bind</button>
    </div>`;
  }).join("");
}

async function bindTask(task_id) {
  const sel = document.getElementById(`bind-${task_id}`);
  const conn_id = parseInt(sel.value);
  if (!conn_id) {
    setStatus("binding-status", "Select a verified connection first", false);
    loadBindings();
    return;
  }
  setStatus("binding-status", `Binding ${task_id} to connection ${conn_id}`, true);
  const r = await api("PUT", `/api/lava/bindings/${task_id}`, { connection_id: conn_id });
  if (r.ok) {
    setStatus("binding-status", `Bound ${task_id} to connection ${conn_id}`, true);
    loadBindings();
  } else {
    setStatus("binding-status", r.detail || r.error || "Binding failed", false);
    loadBindings();
  }
}

// Init
loadConnections();
