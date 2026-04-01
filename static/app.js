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