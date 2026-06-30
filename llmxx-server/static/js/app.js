/*
 * 檔案路徑: rootmedicals-a/llmxx-server/static/js/app.js
 * 產生時間: 2026-06-17 16:10 +08:00
 * 版本: v0.6-交付整理
 * 說明: llmxx-server 工程展示頁靜態資源，用於 /demo/latest 檢視最新 EBM 結果。
 * 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
 * 版本紀錄:
 * - v0.1: 新增 latest-session fetch、evidence table、event log 與 demo controls。
 * - v0.2: JSON/Event 動作改成同頁導頁，避免瀏覽器 popup policy 靜默擋住。
 * - v0.3: 原生連結負責 demo 導頁；JavaScript 只負責資料渲染與 Events href。
 * - v0.4: 因 click checks 發現 anchor 導頁不穩，恢復明確按鈕 handler。
 * - v0.5: 改用原生 GET form，並在取得 latest session id 後只更新 Events form action。
 * - v0.6: 顯示 ICD-10 anchor 與 ICD gate 狀態。
 * 安全筆記:
 * - 只讀 server-minimized /api/demo/latest 與 event endpoints，不讀 raw payload、diagnostics 或截圖。
 * 驗證筆記:
 * - Refresh、Open JSON、Events 已用 manual/browser click checks 覆蓋。
 * ----------------------------------------------------------------------------------------------------
 */

"use strict";

let latestSessionId = "";
let latestEventsUrl = "/api/demo/latest";

function text(id, value) {
  const node = document.getElementById(id);
  if (node) {
    node.textContent = value === undefined || value === null || value === "" ? "-" : String(value);
  }
}

function listText(value) {
  if (!Array.isArray(value) || value.length === 0) {
    return "-";
  }
  return value.join(", ");
}

function score(value) {
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value);
  }
  return number.toFixed(2);
}

function setLight(lightColor) {
  const badge = document.getElementById("lightBadge");
  const light = String(lightColor || "not_evaluable").toLowerCase();
  if (!badge) {
    return;
  }
  badge.textContent = light;
  badge.className = "light";
  if (light === "green") {
    badge.classList.add("light-green");
  } else if (light === "yellow") {
    badge.classList.add("light-yellow");
  } else if (light === "orange") {
    badge.classList.add("light-orange");
  } else {
    badge.classList.add("light-gray");
  }
}

function renderEvidence(comments) {
  const body = document.getElementById("evidenceBody");
  if (!body) {
    return;
  }
  body.innerHTML = "";
  const rows = [];
  if (Array.isArray(comments)) {
    comments.forEach((comment) => {
      const sources = Array.isArray(comment.sources) ? comment.sources : [];
      if (sources.length === 0) {
        rows.push({ comment: comment.comment || "", paper_id: "-", chunk_id: "-", pmid: "-", doi: "-" });
      } else {
        sources.forEach((source) => {
          rows.push({
            comment: comment.comment || "",
            paper_id: source.paper_id || "-",
            chunk_id: source.chunk_id || "-",
            pmid: source.pmid || "-",
            doi: source.doi || "-",
          });
        });
      }
    });
  }
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="5">No evidence available</td></tr>';
    return;
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    ["comment", "paper_id", "chunk_id", "pmid", "doi"].forEach((key) => {
      const td = document.createElement("td");
      td.textContent = row[key] || "-";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

function renderEvents(events) {
  const list = document.getElementById("eventList");
  if (!list) {
    return;
  }
  list.innerHTML = "";
  if (!Array.isArray(events) || events.length === 0) {
    const li = document.createElement("li");
    li.textContent = "No events";
    list.appendChild(li);
    return;
  }
  events.forEach((event) => {
    const li = document.createElement("li");
    li.textContent = `${event.seq || "-"} ${event.stage || "-"} ${event.event_type || "-"}: ${event.message || "-"}`;
    list.appendChild(li);
  });
}

async function loadLatest() {
  const response = await fetch("/api/demo/latest", { cache: "no-store" });
  const payload = await response.json();
  if (!payload.ok) {
    text("statusValue", payload.status || "empty");
    text("subtitle", payload.message || "No sessions yet.");
    setLight("not_evaluable");
    renderEvidence([]);
    renderEvents([]);
    return;
  }
  const session = payload.session || {};
  const result = payload.response || {};
  latestSessionId = session.id || result.session_id || "";
  latestEventsUrl = latestSessionId ? `/api/sessions/${encodeURIComponent(latestSessionId)}/events` : "/api/demo/latest";
  const eventsForm = document.getElementById("eventsForm");
  if (eventsForm) {
    eventsForm.setAttribute("action", latestEventsUrl);
  }
  const clinical = result.clinical_parse || {};
  const ebm = result.ebm || {};
  const adjudication = result.adjudication || {};
  const finalGate = result.final_gate || {};
  const claimVerify = result.claim_verify || {};
  const demoVerifier = result.demo_verifier || {};

  text("statusValue", result.status || session.status);
  text("sessionValue", latestSessionId);
  text("clientValue", result.client_session_id || session.client_session_id);
  text("createdValue", session.created_at);
  text("dxValue", clinical.dx);
  const icdCode = clinical.icd10_code || clinical.icd_code || "-";
  const icdLabel = clinical.diagnosis_label || clinical.icd_label || "";
  text("icdValue", icdLabel ? `${icdCode} / ${icdLabel}` : icdCode);
  text("dxTextValue", clinical.dx_text || clinical.dx || "-");
  text("txValue", clinical.tx);
  text("hxValue", clinical.hx);
  text("confidenceValue", score(clinical.parse_confidence));
  text("missingValue", listText(clinical.missing_fields));
  text("flagsValue", listText(clinical.uncertainty_flags));
  setLight(finalGate.light_color || ebm.light_color);
  text("gateReason", finalGate.reason);
  text("evidenceBacked", finalGate.evidence_backed === true ? "true" : "false");
  text("displayMode", finalGate.display_mode);
  text("reasonCodes", listText(finalGate.reason_codes));
  const icdGate = finalGate.icd_gate || ebm.icd_gate || {};
  text("icdGate", `${icdGate.status || "-"} / ${icdGate.code || "-"} / ${icdGate.reason || "-"}`);
  text("semanticScore", score(adjudication.semantic_alignment_score));
  text("supportScore", score(adjudication.evidence_support_score));
  text("conflictScore", score(adjudication.conflict_score));
  text("riskScore", score(adjudication.risk_score));
  text("claimVerify", `${claimVerify.verdict || "review"} / ${score(claimVerify.overall_claim_support)}`);
  text("demoVerify", `${demoVerifier.verdict || "review"} / ${score(demoVerifier.score)}`);
  renderEvidence(ebm.rag_comments);
  renderEvents(payload.events || []);
}

document.addEventListener("DOMContentLoaded", () => {
  loadLatest().catch((error) => {
    text("statusValue", "load_failed");
    text("gateReason", error && error.message ? error.message : "Load failed");
  });
});
