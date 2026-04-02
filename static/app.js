/* ─────────────────────────────────────────────
   CloudEye Platform — Frontend Logic
   Phase 1: Service Monitor
───────────────────────────────────────────── */

// ── State ──────────────────────────────────────
const state = {
  services:        [],
  filter:          "all",
  editingId:       null,
  deletingId:      null,
  activeHistoryId: null,
  polling:         null,
};

const POLL_INTERVAL_MS = 30_000; // match backend scheduler

// ── DOM refs ───────────────────────────────────
const $ = id => document.getElementById(id);
const servicesBody   = $("servicesBody");
const historyPanel   = $("historyPanel");
const historyBody    = $("historyBody");
const historyTimeline= $("historyTimeline");
const historyTitle   = $("historyTitle");
const modalOverlay   = $("modalOverlay");
const deleteOverlay  = $("deleteOverlay");
const modalError     = $("modalError");
const checkAllBtn    = $("checkAllBtn");
const lastUpdated    = $("lastUpdated");

// ── API helpers ────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

// ── Formatters ─────────────────────────────────
function fmtTime(isoStr) {
  if (!isoStr) return "—";
  try {
    const d = new Date(isoStr + "Z");
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return isoStr; }
}

function fmtMs(ms) {
  if (ms == null) return "—";
  if (ms < 200)  return `<span class="resp-time resp-time--fast">${ms} ms</span>`;
  if (ms < 800)  return `<span class="resp-time resp-time--medium">${ms} ms</span>`;
  return `<span class="resp-time resp-time--slow">${ms} ms</span>`;
}

function statusBadge(status) {
  const s = status || "UNKNOWN";
  const dot = `<span class="dot"></span>`;
  return `<span class="status-badge status-badge--${s}">${dot}${s}</span>`;
}

function nowStr() {
  return new Date().toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit"
  });
}

// ── Render ─────────────────────────────────────
function renderSummary(services) {
  const total    = services.length;
  const up       = services.filter(s => s.current_status === "UP").length;
  const down     = services.filter(s => s.current_status === "DOWN").length;
  const degraded = services.filter(s => s.current_status === "DEGRADED").length;

  $("statTotal").textContent    = total;
  $("statUp").textContent       = up;
  $("statDown").textContent     = down;
  $("statDegraded").textContent = degraded;
  $("serviceCount").textContent = total;
}

function renderTable(services) {
  const filtered = state.filter === "all"
    ? services
    : services.filter(s => s.current_status === state.filter);

  if (filtered.length === 0) {
    servicesBody.innerHTML = `
      <tr class="table-loading">
        <td colspan="6">No services match this filter.</td>
      </tr>`;
    return;
  }

  servicesBody.innerHTML = filtered.map(svc => {
    const typeBadge = svc.use_mock
      ? `<span class="type-badge type-badge--mock">mock</span>`
      : `<span class="type-badge">live</span>`;

    return `
      <tr data-id="${svc.id}">
        <td>
          <div class="svc-name">${escHtml(svc.name)}</div>
          <div class="svc-url">${escHtml(svc.url)}</div>
        </td>
        <td>${statusBadge(svc.current_status)}</td>
        <td>${fmtMs(svc.last_response_ms)}</td>
        <td style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">${fmtTime(svc.last_checked)}</td>
        <td>${typeBadge}</td>
        <td>
          <div class="row-actions" onclick="event.stopPropagation()">
            <button class="action-btn" onclick="pingService(${svc.id})" title="Run check now">↻</button>
            <button class="action-btn" onclick="openEdit(${svc.id})">Edit</button>
            <button class="action-btn action-btn--danger" onclick="openDelete(${svc.id}, '${escHtml(svc.name)}')">Del</button>
          </div>
        </td>
      </tr>`;
  }).join("");

  // Row click → history
  servicesBody.querySelectorAll("tr[data-id]").forEach(row => {
    row.addEventListener("click", () => {
      loadHistory(parseInt(row.dataset.id));
    });
  });
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Data loading ───────────────────────────────
async function loadServices() {
  try {
    const [services] = await Promise.all([api("/api/services")]);
    state.services = services;
    renderSummary(services);
    renderTable(services);
    lastUpdated.textContent = `Updated ${nowStr()}`;
  } catch (e) {
    servicesBody.innerHTML = `
      <tr class="table-loading">
        <td colspan="6" style="color:var(--down)">Failed to load: ${escHtml(e.message)}</td>
      </tr>`;
  }
}

async function loadHistory(serviceId) {
  const svc = state.services.find(s => s.id === serviceId);
  state.activeHistoryId = serviceId;
  historyTitle.textContent = `Check history — ${svc?.name ?? serviceId}`;
  historyPanel.style.display = "block";
  historyBody.innerHTML = `<tr class="table-loading"><td colspan="5">Loading…</td></tr>`;
  historyTimeline.innerHTML = "";

  try {
    const rows = await api(`/api/services/${serviceId}/history?limit=40`);

    // Timeline ticks (most recent rightmost)
    historyTimeline.innerHTML = [...rows].reverse().map(r =>
      `<div class="timeline-tick timeline-tick--${r.status || "UNKNOWN"}" title="${r.checked_at} — ${r.status}"></div>`
    ).join("");

    if (rows.length === 0) {
      historyBody.innerHTML = `<tr class="table-loading"><td colspan="5">No checks yet.</td></tr>`;
      return;
    }

    historyBody.innerHTML = rows.map(r => `
      <tr>
        <td style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">${r.checked_at}</td>
        <td>${statusBadge(r.status)}</td>
        <td>${fmtMs(r.response_time_ms)}</td>
        <td style="font-family:var(--font-mono);font-size:11px;color:var(--text-dim)">${r.status_code ?? "—"}</td>
        <td style="font-family:var(--font-mono);font-size:11px;color:var(--down)">${escHtml(r.error_message ?? "")}</td>
      </tr>`).join("");

    historyPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    historyBody.innerHTML = `<tr class="table-loading"><td colspan="5" style="color:var(--down)">Error: ${escHtml(e.message)}</td></tr>`;
  }
}

// ── Check all / manual ping ────────────────────
checkAllBtn.addEventListener("click", async () => {
  const icon = checkAllBtn.querySelector(".btn-icon");
  icon.classList.add("spinning");
  checkAllBtn.disabled = true;
  try {
    await api("/api/check-all", { method: "POST" });
    await loadServices();
    if (state.activeHistoryId) await loadHistory(state.activeHistoryId);
  } catch (e) {
    console.error(e);
  } finally {
    icon.classList.remove("spinning");
    checkAllBtn.disabled = false;
  }
});

window.pingService = async function(id) {
  try {
    await api(`/api/services/${id}/check`, { method: "POST" });
    await loadServices();
  } catch (e) { console.error(e); }
};

// ── Filters ────────────────────────────────────
document.querySelectorAll(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.filter;
    renderTable(state.services);
  });
});

// ── History panel close ────────────────────────
$("closeHistoryBtn").addEventListener("click", () => {
  historyPanel.style.display = "none";
  state.activeHistoryId = null;
});

// ── Add/Edit modal ─────────────────────────────
function openModal(svc = null) {
  state.editingId = svc ? svc.id : null;
  $("modalTitle").textContent = svc ? "Edit service" : "Add service";
  $("fieldName").value        = svc?.name       ?? "";
  $("fieldUrl").value         = svc?.url        ?? "";
  $("fieldMock").checked      = !!svc?.use_mock;
  $("fieldMockStatus").value  = svc?.mock_status ?? "UP";
  $("mockStatusField").style.display = $("fieldMock").checked ? "" : "none";
  $("modalSaveBtn").textContent = svc ? "Update service" : "Save service";
  modalError.style.display = "none";
  modalOverlay.style.display = "flex";
  $("fieldName").focus();
}

window.openEdit = async function(id) {
  try {
    const svc = await api(`/api/services/${id}`);
    openModal(svc);
  } catch (e) { console.error(e); }
};

$("addServiceBtn").addEventListener("click", () => openModal(null));

$("fieldMock").addEventListener("change", e => {
  $("mockStatusField").style.display = e.target.checked ? "" : "none";
});

function closeModal() {
  modalOverlay.style.display = "none";
  state.editingId = null;
}

$("modalClose").addEventListener("click", closeModal);
$("modalCancelBtn").addEventListener("click", closeModal);
modalOverlay.addEventListener("click", e => { if (e.target === modalOverlay) closeModal(); });

$("modalSaveBtn").addEventListener("click", async () => {
  const name       = $("fieldName").value.trim();
  const url        = $("fieldUrl").value.trim();
  const use_mock   = $("fieldMock").checked;
  const mock_status= $("fieldMockStatus").value;

  if (!name || !url) {
    showModalError("Name and URL are required.");
    return;
  }

  const body = JSON.stringify({ name, url, use_mock, mock_status });
  try {
    if (state.editingId) {
      await api(`/api/services/${state.editingId}`, { method: "PATCH", body });
    } else {
      await api("/api/services", { method: "POST", body });
    }
    closeModal();
    await loadServices();
  } catch (e) {
    showModalError(e.message);
  }
});

function showModalError(msg) {
  modalError.textContent = msg;
  modalError.style.display = "block";
}

// ── Delete modal ───────────────────────────────
window.openDelete = function(id, name) {
  state.deletingId = id;
  $("deleteServiceName").textContent = name;
  deleteOverlay.style.display = "flex";
};

function closeDelete() {
  deleteOverlay.style.display = "none";
  state.deletingId = null;
}

$("deleteClose").addEventListener("click", closeDelete);
$("deleteCancelBtn").addEventListener("click", closeDelete);
deleteOverlay.addEventListener("click", e => { if (e.target === deleteOverlay) closeDelete(); });

$("deleteConfirmBtn").addEventListener("click", async () => {
  if (!state.deletingId) return;
  try {
    await api(`/api/services/${state.deletingId}`, { method: "DELETE" });
    closeDelete();
    if (state.activeHistoryId === state.deletingId) {
      historyPanel.style.display = "none";
      state.activeHistoryId = null;
    }
    await loadServices();
  } catch (e) {
    console.error(e);
  }
});

// ── Sidebar navigation ─────────────────────────
document.querySelectorAll(".nav-item:not(.disabled)").forEach(item => {
  item.addEventListener("click", e => {
    e.preventDefault();
    const section = item.dataset.section;
    document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
    item.classList.add("active");
    document.querySelectorAll(".section").forEach(s => s.style.display = "none");
    $(`section-${section}`).style.display = "";
    $("breadcrumbCurrent").textContent = item.querySelector(".nav-label").textContent;
    // Close mobile sidebar
    $("sidebar").classList.remove("open");
  });
});

// ── Mobile sidebar toggle ──────────────────────
const mobileMenuBtn = $("mobileMenuBtn");
const sidebar = $("sidebar");

mobileMenuBtn.addEventListener("click", () => {
  sidebar.classList.toggle("open");
});

document.addEventListener("click", e => {
  if (!sidebar.contains(e.target) && !mobileMenuBtn.contains(e.target)) {
    sidebar.classList.remove("open");
  }
});

// ── Keyboard shortcuts ─────────────────────────
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    closeModal();
    closeDelete();
  }
});

// ── Auto-polling ───────────────────────────────
function startPolling() {
  state.polling = setInterval(async () => {
    await loadServices();
    if (state.activeHistoryId) {
      await loadHistory(state.activeHistoryId);
    }
  }, POLL_INTERVAL_MS);
}

// ── Init ───────────────────────────────────────
(async function init() {
  await loadServices();
  startPolling();
})();


/* ──────────────────────────────────────────────
   Phase 2 — Log Analyzer
────────────────────────────────────────────── */

const logState = {
  file:          null,
  result:        null,
  lineFilter:    "ERROR",
};

// ── DOM refs ────────────────────────────────────
const logDropZone      = $("logDropZone");
const logFileInput     = $("logFileInput");
const uploadMeta       = $("uploadMeta");
const analyzeBtn       = $("analyzeBtn");
const logResultPanel   = $("logResultPanel");
const logSummaryRow    = $("logSummaryRow");
const logMetaRow       = $("logMetaRow");
const logIssuesWrap    = $("logIssuesWrap");
const logIssuesList    = $("logIssuesList");
const logLinesWrap     = $("logLinesWrap");
const logLinesBody     = $("logLinesBody");
const errorLineCount   = $("errorLineCount");
const resultFilename   = $("resultFilename");
const logHistoryBody   = $("logHistoryBody");

// ── File selection ──────────────────────────────
function setLogFile(file) {
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) {
    uploadMeta.textContent = "File too large — max 10 MB.";
    uploadMeta.style.color = "var(--down)";
    analyzeBtn.disabled = true;
    return;
  }
  logState.file = file;
  const kb = (file.size / 1024).toFixed(1);
  uploadMeta.textContent = `${file.name}  (${kb} KB)`;
  uploadMeta.style.color = "var(--accent)";
  analyzeBtn.disabled = false;
}

logFileInput.addEventListener("change", () => {
  if (logFileInput.files[0]) setLogFile(logFileInput.files[0]);
});

// Drag & drop
logDropZone.addEventListener("dragover", e => {
  e.preventDefault();
  logDropZone.classList.add("drag-over");
});
logDropZone.addEventListener("dragleave", () => logDropZone.classList.remove("drag-over"));
logDropZone.addEventListener("drop", e => {
  e.preventDefault();
  logDropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) setLogFile(file);
});

// ── Analyze ─────────────────────────────────────
analyzeBtn.addEventListener("click", async () => {
  if (!logState.file) return;

  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing…";

  const formData = new FormData();
  formData.append("file", logState.file);

  try {
    const res = await fetch("/api/logs/analyze", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Upload failed" }));
      throw new Error(err.detail);
    }
    const result = await res.json();
    logState.result = result;
    renderLogResult(result, logState.file.name);
    loadLogHistory();
  } catch (e) {
    uploadMeta.textContent = `Error: ${e.message}`;
    uploadMeta.style.color = "var(--down)";
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze";
  }
});

// ── Render result ───────────────────────────────
function renderLogResult(r, filename) {
  resultFilename.textContent = filename;

  // Summary stats
  logSummaryRow.innerHTML = `
    <div class="log-stat log-stat--error">
      <div class="log-stat-label">Errors</div>
      <div class="log-stat-value">${r.counts.ERROR}</div>
    </div>
    <div class="log-stat log-stat--critical">
      <div class="log-stat-label">Critical</div>
      <div class="log-stat-value">${r.counts.CRITICAL}</div>
    </div>
    <div class="log-stat log-stat--warning">
      <div class="log-stat-label">Warnings</div>
      <div class="log-stat-value">${r.counts.WARNING}</div>
    </div>
    <div class="log-stat log-stat--info">
      <div class="log-stat-label">Info</div>
      <div class="log-stat-value">${r.counts.INFO}</div>
    </div>
    <div class="log-stat log-stat--debug">
      <div class="log-stat-label">Debug</div>
      <div class="log-stat-value">${r.counts.DEBUG}</div>
    </div>`;

  // Meta row
  const from = r.time_range?.from
    ? new Date(r.time_range.from).toLocaleString()
    : "—";
  const to = r.time_range?.to
    ? new Date(r.time_range.to).toLocaleString()
    : "—";

  logMetaRow.innerHTML = `
    <div class="log-meta-item">
      <div class="log-meta-label">Total lines</div>
      <div class="log-meta-value">${r.total_lines.toLocaleString()}</div>
    </div>
    <div class="log-meta-item">
      <div class="log-meta-label">Parsed</div>
      <div class="log-meta-value">${r.parsed_lines.toLocaleString()}</div>
    </div>
    <div class="log-meta-item">
      <div class="log-meta-label">Time range from</div>
      <div class="log-meta-value">${from}</div>
    </div>
    <div class="log-meta-item">
      <div class="log-meta-label">To</div>
      <div class="log-meta-value">${to}</div>
    </div>
    ${r.most_frequent_issue ? `
    <div class="log-meta-item">
      <div class="log-meta-label">Most frequent issue</div>
      <div class="log-meta-value log-meta-value--highlight">${escHtml(r.most_frequent_issue)}</div>
    </div>` : ""}`;

  // Top issues
  if (r.top_issues?.length) {
    const maxCount = r.top_issues[0].count;
    logIssuesList.innerHTML = r.top_issues.map(issue => {
      const pct = maxCount > 0 ? Math.round((issue.count / maxCount) * 100) : 0;
      return `
        <div class="log-issue-row">
          <div class="log-issue-label" title="${escHtml(issue.issue)}">${escHtml(issue.issue)}</div>
          <div class="log-issue-bar-wrap">
            <div class="log-issue-bar" style="width:${pct}%"></div>
          </div>
          <div class="log-issue-count">${issue.count}</div>
        </div>`;
    }).join("");
    logIssuesWrap.style.display = "";
  } else {
    logIssuesWrap.style.display = "none";
  }

  // Error lines (default view)
  logState.lineFilter = "ERROR";
  document.querySelectorAll("#lineFilter .filter-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.lf === "ERROR");
  });
  renderLogLines(r);

  logResultPanel.style.display = "";
  logResultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderLogLines(r) {
  const lines = logState.lineFilter === "ERROR"
    ? (r.error_lines   || [])
    : (r.warning_lines || []);

  errorLineCount.textContent = lines.length;

  if (!lines.length) {
    logLinesBody.innerHTML = `<tr class="table-loading"><td colspan="4">No ${logState.lineFilter.toLowerCase()} lines found.</td></tr>`;
    logLinesWrap.style.display = "";
    return;
  }

  logLinesBody.innerHTML = lines.map(l => `
    <tr>
      <td>${escHtml(l.timestamp)}</td>
      <td><span class="status-badge log-level--${l.level}" style="font-size:10px;padding:2px 6px">${l.level}</span></td>
      <td>${escHtml(l.logger || "—")}</td>
      <td>${escHtml(l.message)}</td>
    </tr>`).join("");

  logLinesWrap.style.display = "";
}

// Line filter toggle
document.querySelectorAll("#lineFilter .filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#lineFilter .filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    logState.lineFilter = btn.dataset.lf;
    if (logState.result) renderLogLines(logState.result);
  });
});

// Clear result
$("clearResultBtn").addEventListener("click", () => {
  logResultPanel.style.display = "none";
  logState.result = null;
  logState.file = null;
  logFileInput.value = "";
  uploadMeta.textContent = "";
  analyzeBtn.disabled = true;
});

// ── Upload history ───────────────────────────────
async function loadLogHistory() {
  try {
    const rows = await api("/api/logs/history?limit=15");
    if (!rows.length) {
      logHistoryBody.innerHTML = `<tr class="table-loading"><td colspan="7">No uploads yet.</td></tr>`;
      return;
    }
    logHistoryBody.innerHTML = rows.map(r => `
      <tr>
        <td><div class="log-history-filename">${escHtml(r.filename)}</div></td>
        <td><div class="log-history-time">${r.uploaded_at}</div></td>
        <td style="font-family:var(--font-mono);font-size:12px">${r.total_lines?.toLocaleString() ?? "—"}</td>
        <td style="font-family:var(--font-mono);font-size:12px;color:var(--down)">${r.count_error ?? 0}</td>
        <td style="font-family:var(--font-mono);font-size:12px;color:var(--degraded)">${r.count_warning ?? 0}</td>
        <td style="font-family:var(--font-mono);font-size:12px;color:var(--text-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(r.most_frequent_issue ?? "—")}</td>
        <td>
          <div class="row-actions">
            <button class="action-btn" onclick="loadHistoryResult(${r.id}, '${escHtml(r.filename)}')">View</button>
            <button class="action-btn action-btn--danger" onclick="deleteLogUpload(${r.id})">Del</button>
          </div>
        </td>
      </tr>`).join("");
  } catch (e) {
    logHistoryBody.innerHTML = `<tr class="table-loading"><td colspan="7" style="color:var(--down)">Failed to load history.</td></tr>`;
  }
}

window.loadHistoryResult = async function(id, filename) {
  try {
    const result = await api(`/api/logs/${id}`);
    logState.result = result;
    renderLogResult(result, filename);
  } catch (e) { console.error(e); }
};

window.deleteLogUpload = async function(id) {
  try {
    await api(`/api/logs/${id}`, { method: "DELETE" });
    loadLogHistory();
    if (logState.result?.upload_id === id) {
      logResultPanel.style.display = "none";
      logState.result = null;
    }
  } catch (e) { console.error(e); }
};

$("refreshHistoryBtn").addEventListener("click", loadLogHistory);

// Load history when Log Analyzer tab is opened
document.querySelectorAll(".nav-item").forEach(item => {
  item.addEventListener("click", () => {
    if (item.dataset.section === "logs") {
      loadLogHistory();
    }
  });
});