// ── Load Testing Page Controller ──────────────────────────────────────────────
(function () {
  "use strict";

  const API_BASE = window.TRIAGE_API_BASE || "";
  const LT_BASE = API_BASE ? `${API_BASE}/ops/loadtest` : "/ops/loadtest";
  const POLL_INTERVAL_MS = 2000;

  const HEADERS = {
    "Content-Type": "application/json",
    "X-TRIAGE-SECRET": window.TRIAGE_SHARED_SECRET || "turbohedi-shared-secret",
  };

  // ── Element refs ────────────────────────────────────────────────────────────
  const profileGroup    = document.getElementById("ltProfileGroup");
  const typesGroup      = document.getElementById("ltTypesGroup");
  const sizesSection    = document.getElementById("ltSizesSection");
  const sizesGroup      = document.getElementById("ltSizesGroup");
  const concurrencyEl   = document.getElementById("ltConcurrency");
  const concurrencyVal  = document.getElementById("ltConcurrencyValue");
  const adversarialEl   = document.getElementById("ltAdversarial");
  const multiPartEl     = document.getElementById("ltMultiPart");
  const startBtn        = document.getElementById("ltStartBtn");
  const stopBtn         = document.getElementById("ltStopBtn");
  const exportBtn       = document.getElementById("ltExportBtn");
  const statusPill      = document.getElementById("ltStatusPill");
  const progressFill    = document.getElementById("ltProgressFill");
  const progressPct     = document.getElementById("ltProgressPct");
  const resultsBody     = document.getElementById("ltResultsBody");
  const summaryCard     = document.getElementById("ltSummaryCard");
  const summaryGrid     = document.getElementById("ltSummaryGrid");
  const breakdownCtr    = document.getElementById("ltBreakdownContainer");
  const errorBanner     = document.getElementById("ltErrorBanner");

  let pollTimer = null;
  let lastStatusData = null;

  // ── Utilities ───────────────────────────────────────────────────────────────
  function escHtml(v) {
    return String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function showError(msg) {
    errorBanner.textContent = msg;
    errorBanner.hidden = false;
  }

  function hideError() {
    errorBanner.hidden = true;
    errorBanner.textContent = "";
  }

  function formatMs(ms) {
    if (ms == null) return "—";
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  function formatBytes(bytes) {
    if (bytes == null) return "—";
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}K`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)}GB`;
  }

  // ── Profile → sizes toggle ──────────────────────────────────────────────────
  function getSelectedProfile() {
    const radio = profileGroup.querySelector('input[name="lt-profile"]:checked');
    return radio ? radio.value : "smoke";
  }

  function updateSizeVisibility() {
    const profile = getSelectedProfile();
    if (profile === "custom") {
      sizesSection.classList.add("lt-show");
    } else {
      sizesSection.classList.remove("lt-show");
    }
  }

  profileGroup.addEventListener("change", updateSizeVisibility);
  updateSizeVisibility();

  // ── Concurrency slider sync ─────────────────────────────────────────────────
  concurrencyEl.addEventListener("input", () => {
    concurrencyVal.textContent = concurrencyEl.value;
  });

  // ── Collect config from form ────────────────────────────────────────────────
  function collectConfig() {
    const profile = getSelectedProfile();
    const types = Array.from(typesGroup.querySelectorAll("input:checked")).map(cb => cb.value);
    const sizes = profile === "custom"
      ? Array.from(sizesGroup.querySelectorAll("input:checked")).map(cb => cb.value)
      : [];

    return {
      profile,
      types,
      sizes,
      concurrency: parseInt(concurrencyEl.value, 10),
      include_adversarial: adversarialEl.checked,
      include_multi_part: multiPartEl.checked,
    };
  }

  // ── Status pill update ──────────────────────────────────────────────────────
  function setStatus(status) {
    const labels = { idle: "Idle", running: "Running…", complete: "Complete", stopped: "Stopped", error: "Error" };
    statusPill.textContent = labels[status] || status;
    statusPill.className = `lt-status-pill lt-status-pill--${status}`;
  }

  // ── Button state management ─────────────────────────────────────────────────
  function setRunning(running) {
    startBtn.disabled = running;
    stopBtn.disabled = !running;
    if (running) {
      startBtn.innerHTML = '<span class="lt-spinner" aria-hidden="true"></span>Running…';
    } else {
      startBtn.textContent = "Start Test Run";
    }
  }

  // ── Results table rendering ─────────────────────────────────────────────────
  function renderResults(results) {
    if (!results || results.length === 0) {
      resultsBody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:1.5rem;">Waiting for results…</td></tr>';
      return;
    }

    resultsBody.innerHTML = results.map(r => {
      const statusClass = `lt-result-status--${r.status || "queued"}`;
      const resultClass = r.result === "pass" ? "lt-result-pass" : r.result === "fail" ? "lt-result-fail" : "";
      const resultText = r.result || "—";
      const errorInfo = r.error ? ` title="${escHtml(r.error)}"` : "";

      return `<tr>
        <td>${escHtml(r.file)}</td>
        <td>${escHtml((r.type || "").toUpperCase())}</td>
        <td>${formatBytes(r.size_bytes)}</td>
        <td><span class="lt-result-status ${statusClass}">${escHtml(r.status)}</span></td>
        <td>${formatMs(r.upload_ms)}</td>
        <td>${formatMs(r.processing_ms)}</td>
        <td><span class="${resultClass}"${errorInfo}>${escHtml(resultText)}</span></td>
      </tr>`;
    }).join("");
  }

  // ── Summary rendering ──────────────────────────────────────────────────────
  function renderSummary(summary) {
    if (!summary) {
      summaryCard.hidden = true;
      return;
    }
    summaryCard.hidden = false;

    summaryGrid.innerHTML = `
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value">${summary.total || 0}</span>
        <span class="lt-summary-stat-label">Total Files</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value" style="color:#047857">${summary.passed || 0}</span>
        <span class="lt-summary-stat-label">Passed</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value" style="color:var(--err)">${summary.failed || 0}</span>
        <span class="lt-summary-stat-label">Failed</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value">${summary.avg_throughput_mbps != null ? summary.avg_throughput_mbps.toFixed(2) : "—"}</span>
        <span class="lt-summary-stat-label">Avg MB/s</span>
      </div>`;

    let breakdownHtml = "";

    if (summary.by_type && Object.keys(summary.by_type).length > 0) {
      breakdownHtml += '<div class="lt-breakdown-section"><div class="lt-breakdown-title">By Transaction Type</div><div class="lt-breakdown-grid">';
      for (const [type, stats] of Object.entries(summary.by_type)) {
        breakdownHtml += `<div class="lt-breakdown-item"><strong>${escHtml(type.toUpperCase())}</strong>: ${stats.passed || 0}✓ / ${stats.failed || 0}✗</div>`;
      }
      breakdownHtml += "</div></div>";
    }

    if (summary.by_size && Object.keys(summary.by_size).length > 0) {
      breakdownHtml += '<div class="lt-breakdown-section"><div class="lt-breakdown-title">By Size</div><div class="lt-breakdown-grid">';
      for (const [size, stats] of Object.entries(summary.by_size)) {
        breakdownHtml += `<div class="lt-breakdown-item"><strong>${escHtml(size)}</strong>: ${stats.passed || 0}✓ / ${stats.failed || 0}✗</div>`;
      }
      breakdownHtml += "</div></div>";
    }

    if (summary.adversarial && Object.keys(summary.adversarial).length > 0) {
      breakdownHtml += '<div class="lt-breakdown-section"><div class="lt-breakdown-title">Adversarial Tests</div><div class="lt-breakdown-grid">';
      const adv = summary.adversarial;
      breakdownHtml += `<div class="lt-breakdown-item">Expected rejections: <strong>${adv.expected_rejections || 0}</strong></div>`;
      breakdownHtml += `<div class="lt-breakdown-item">Correctly rejected: <strong>${adv.correctly_rejected || 0}</strong></div>`;
      if (adv.incorrectly_accepted) {
        breakdownHtml += `<div class="lt-breakdown-item" style="color:var(--err)">Incorrectly accepted: <strong>${adv.incorrectly_accepted}</strong></div>`;
      }
      breakdownHtml += "</div></div>";
    }

    breakdownCtr.innerHTML = breakdownHtml;
  }

  // ── Progress update ─────────────────────────────────────────────────────────
  function updateProgress(data) {
    lastStatusData = data;

    setStatus(data.status || "idle");

    const progress = data.progress || {};
    const pct = progress.pct || 0;
    progressFill.style.width = `${pct}%`;
    progressPct.textContent = `${Math.round(pct)}% (${progress.completed || 0} / ${progress.total || 0})`;

    renderResults(data.results);

    if (data.status === "complete" || data.status === "stopped" || data.status === "error") {
      setRunning(false);
      stopPolling();
      exportBtn.disabled = !(data.results && data.results.length > 0);
      if (data.summary) {
        renderSummary(data.summary);
      }
    }
  }

  // ── API calls ───────────────────────────────────────────────────────────────
  async function startTest() {
    hideError();
    const config = collectConfig();

    if (config.types.length === 0) {
      showError("Please select at least one transaction type.");
      return;
    }

    setRunning(true);
    setStatus("running");
    summaryCard.hidden = true;
    renderResults([]);

    try {
      const res = await fetch(`${LT_BASE}/start`, {
        method: "POST",
        headers: HEADERS,
        body: JSON.stringify(config),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(`${res.status}: ${text}`);
      }

      startPolling();
    } catch (err) {
      showError(`Failed to start test: ${err.message}`);
      setRunning(false);
      setStatus("error");
    }
  }

  async function pollStatus() {
    try {
      const res = await fetch(`${LT_BASE}/status`, { headers: HEADERS });
      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`);
      }
      const data = await res.json();
      updateProgress(data);
    } catch (err) {
      console.error("Status poll failed:", err);
    }
  }

  async function stopTest() {
    try {
      const res = await fetch(`${LT_BASE}/stop`, {
        method: "POST",
        headers: HEADERS,
      });
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(`${res.status}: ${text}`);
      }
      // Next poll will pick up stopped state
    } catch (err) {
      showError(`Failed to stop test: ${err.message}`);
    }
  }

  function exportResults() {
    if (!lastStatusData) return;
    const blob = new Blob([JSON.stringify(lastStatusData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `loadtest-results-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // ── Polling ─────────────────────────────────────────────────────────────────
  function startPolling() {
    stopPolling();
    pollStatus(); // immediate first poll
    pollTimer = setInterval(pollStatus, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ── Event bindings ──────────────────────────────────────────────────────────
  startBtn.addEventListener("click", startTest);
  stopBtn.addEventListener("click", stopTest);
  exportBtn.addEventListener("click", exportResults);

  // ── Initial status check ───────────────────────────────────
  // On page load, check if there's already a running test
  (async function checkInitialStatus() {
    try {
      const res = await fetch(`${LT_BASE}/status`, { headers: HEADERS });
      if (res.ok) {
        const data = await res.json();
        if (data.status === "running") {
          updateProgress(data);
          setRunning(true);
          startPolling();
        } else if (data.status === "complete" || data.status === "stopped") {
          updateProgress(data);
        }
      }
    } catch {
      // Silently ignore — server might not be running yet
    }
  })();

  // ═══════════════════════════════════════════════════════════════
  // Tab switching + Swarm Pipeline controller
  // ═══════════════════════════════════════════════════════════════

  const SWARM_BASE = `${LT_BASE}/swarm`;

  const tabButtons = document.querySelectorAll(".lt-tab");
  const tabPanels = {
    http: document.getElementById("ltTabHttp"),
    swarm: document.getElementById("ltTabSwarm"),
  };

  function activateTab(name) {
    tabButtons.forEach(btn => {
      btn.setAttribute("aria-selected", btn.dataset.tab === name ? "true" : "false");
    });
    Object.entries(tabPanels).forEach(([key, panel]) => {
      if (panel) panel.hidden = key !== name;
    });
    if (name === "swarm") swarmPollOnce();
  }
  tabButtons.forEach(btn => btn.addEventListener("click", () => activateTab(btn.dataset.tab)));

  // ── Swarm DOM refs ───────────────────────────────────────────────
  const swarmProfileGroup    = document.getElementById("ltSwarmProfileGroup");
  const swarmTypesGroup      = document.getElementById("ltSwarmTypesGroup");
  const swarmSizesSection    = document.getElementById("ltSwarmSizesSection");
  const swarmSizesGroup      = document.getElementById("ltSwarmSizesGroup");
  const swarmPoolEl          = document.getElementById("ltSwarmPool");
  const swarmWorkersEl       = document.getElementById("ltSwarmWorkers");
  const swarmRepeatsEl       = document.getElementById("ltSwarmRepeats");
  const swarmClaimsPerBatch  = document.getElementById("ltSwarmClaimsPerBatch");
  const swarmConcurrencyEl   = document.getElementById("ltSwarmConcurrency");
  const swarmStartBtn        = document.getElementById("ltSwarmStartBtn");
  const swarmStopBtn         = document.getElementById("ltSwarmStopBtn");
  const swarmExportBtn       = document.getElementById("ltSwarmExportBtn");
  const swarmStatusPill      = document.getElementById("ltSwarmStatusPill");
  const swarmProgressFill    = document.getElementById("ltSwarmProgressFill");
  const swarmProgressPct     = document.getElementById("ltSwarmProgressPct");
  const swarmResultsBody     = document.getElementById("ltSwarmResultsBody");
  const swarmSummaryCard     = document.getElementById("ltSwarmSummaryCard");
  const swarmSummaryGrid     = document.getElementById("ltSwarmSummaryGrid");
  const swarmBreakdownCtr    = document.getElementById("ltSwarmBreakdownContainer");

  let swarmPollTimer = null;
  let swarmLastData = null;

  function getSwarmProfile() {
    const radio = swarmProfileGroup.querySelector('input[name="lt-swarm-profile"]:checked');
    return radio ? radio.value : "quick";
  }

  function updateSwarmSizeVisibility() {
    const profile = getSwarmProfile();
    swarmSizesSection.classList.toggle("lt-show", profile === "custom");
  }
  swarmProfileGroup.addEventListener("change", updateSwarmSizeVisibility);
  updateSwarmSizeVisibility();

  function collectSwarmConfig() {
    const profile = getSwarmProfile();
    const types = Array.from(swarmTypesGroup.querySelectorAll("input:checked")).map(cb => cb.value);
    const sizes = profile === "custom"
      ? Array.from(swarmSizesGroup.querySelectorAll("input:checked")).map(cb => cb.value)
      : [];
    return {
      profile,
      types,
      sizes,
      repeats: Math.max(1, parseInt(swarmRepeatsEl.value, 10) || 3),
      concurrency: Math.max(1, parseInt(swarmConcurrencyEl.value, 10) || 1),
      pool_mode: swarmPoolEl.value,
      max_workers: Math.max(1, parseInt(swarmWorkersEl.value, 10) || 4),
      claims_per_batch: Math.max(1, parseInt(swarmClaimsPerBatch.value, 10) || 25),
    };
  }

  function setSwarmStatus(status) {
    const labels = { idle: "Idle", running: "Running…", complete: "Complete", stopped: "Stopped", error: "Error" };
    swarmStatusPill.textContent = labels[status] || status;
    swarmStatusPill.className = `lt-status-pill lt-status-pill--${status}`;
  }

  function setSwarmRunning(running) {
    swarmStartBtn.disabled = running;
    swarmStopBtn.disabled = !running;
    if (running) {
      swarmStartBtn.innerHTML = '<span class="lt-spinner" aria-hidden="true"></span>Running…';
    } else {
      swarmStartBtn.textContent = "Start Swarm Run";
    }
  }

  function renderSwarmResults(results) {
    if (!results || results.length === 0) {
      swarmResultsBody.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:1.5rem;">Waiting for results…</td></tr>';
      return;
    }
    swarmResultsBody.innerHTML = results.map((r, idx) => {
      const statusClass = `lt-result-status--${r.status || "queued"}`;
      const resultClass = r.result === "pass" ? "lt-result-pass" : r.result === "fail" ? "lt-result-fail" : "";
      const baselineP50 = r.baseline && r.baseline.p50_ms != null ? r.baseline.p50_ms : null;
      const swarmP50 = r.swarm && r.swarm.p50_ms != null ? r.swarm.p50_ms : null;
      let speedupCell = "—";
      if (typeof r.p50_speedup_x === "number") {
        const cls = r.p50_speedup_x >= 1 ? "lt-speedup--win" : "lt-speedup--loss";
        speedupCell = `<span class="lt-speedup ${cls}">${r.p50_speedup_x.toFixed(2)}x</span>`;
      }
      const errorInfo = r.error ? ` title="${escHtml(r.error)}"` : "";
      return `<tr>
        <td>${idx + 1}</td>
        <td>${escHtml((r.type || "").toUpperCase())}</td>
        <td>${formatBytes(r.size_bytes)}</td>
        <td><span class="lt-result-status ${statusClass}">${escHtml(r.status || "queued")}</span></td>
        <td>${baselineP50 != null ? formatMs(baselineP50) : "—"}</td>
        <td>${swarmP50 != null ? formatMs(swarmP50) : "—"}</td>
        <td>${speedupCell}</td>
        <td><span class="${resultClass}"${errorInfo}>${escHtml(r.result || "—")}</span></td>
      </tr>`;
    }).join("");
  }

  function renderSwarmSummary(summary) {
    if (!summary) {
      swarmSummaryCard.hidden = true;
      return;
    }
    swarmSummaryCard.hidden = false;
    const avgSpeedup = summary.avg_speedup_x != null ? summary.avg_speedup_x.toFixed(2) : "—";
    swarmSummaryGrid.innerHTML = `
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value">${summary.total || 0}</span>
        <span class="lt-summary-stat-label">Cells</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value" style="color:#047857">${summary.passed || 0}</span>
        <span class="lt-summary-stat-label">Passed</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value" style="color:var(--err)">${summary.failed || 0}</span>
        <span class="lt-summary-stat-label">Failed</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value">${avgSpeedup}x</span>
        <span class="lt-summary-stat-label">Avg speedup (p50)</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value">${escHtml(summary.pool_mode || "—")}</span>
        <span class="lt-summary-stat-label">Pool</span>
      </div>
      <div class="lt-summary-stat">
        <span class="lt-summary-stat-value">${summary.max_workers || 0}</span>
        <span class="lt-summary-stat-label">Workers</span>
      </div>`;

    let breakdownHtml = "";
    if (summary.by_size && Object.keys(summary.by_size).length > 0) {
      breakdownHtml += '<div class="lt-breakdown-section"><div class="lt-breakdown-title">Speedup by Size</div><div class="lt-breakdown-grid">';
      for (const [size, info] of Object.entries(summary.by_size)) {
        const sp = info.p50_speedup_x != null ? info.p50_speedup_x.toFixed(2) : "—";
        breakdownHtml += `<div class="lt-breakdown-item"><strong>${escHtml(size)}</strong>: ${sp}x × ${info.runs || 0} run(s)</div>`;
      }
      breakdownHtml += "</div></div>";
    }
    if (summary.by_type && Object.keys(summary.by_type).length > 0) {
      breakdownHtml += '<div class="lt-breakdown-section"><div class="lt-breakdown-title">By Transaction Type</div><div class="lt-breakdown-grid">';
      for (const [t, info] of Object.entries(summary.by_type)) {
        breakdownHtml += `<div class="lt-breakdown-item"><strong>${escHtml(t.toUpperCase())}</strong>: ${info.passed || 0}✓ / ${info.failed || 0}✗</div>`;
      }
      breakdownHtml += "</div></div>";
    }
    swarmBreakdownCtr.innerHTML = breakdownHtml;
  }

  function updateSwarmProgress(data) {
    swarmLastData = data;
    setSwarmStatus(data.status || "idle");
    const progress = data.progress || {};
    const pct = progress.pct || 0;
    swarmProgressFill.style.width = `${pct}%`;
    swarmProgressPct.textContent = `${Math.round(pct)}% (${progress.completed || 0} / ${progress.total || 0})`;
    renderSwarmResults(data.results);
    if (["complete", "stopped", "error"].includes(data.status)) {
      setSwarmRunning(false);
      stopSwarmPolling();
      swarmExportBtn.disabled = !(data.results && data.results.length > 0);
      renderSwarmSummary(data.summary);
    }
  }

  async function startSwarmRun() {
    hideError();
    const config = collectSwarmConfig();
    if (config.types.length === 0) {
      showError("Please select at least one transaction type for the swarm run.");
      return;
    }
    setSwarmRunning(true);
    setSwarmStatus("running");
    swarmSummaryCard.hidden = true;
    renderSwarmResults([]);
    try {
      const res = await fetch(`${SWARM_BASE}/start`, {
        method: "POST",
        headers: HEADERS,
        body: JSON.stringify(config),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(`${res.status}: ${text}`);
      }
      startSwarmPolling();
    } catch (err) {
      showError(`Failed to start swarm test: ${err.message}`);
      setSwarmRunning(false);
      setSwarmStatus("error");
    }
  }

  async function pollSwarmStatus() {
    try {
      const res = await fetch(`${SWARM_BASE}/status`, { headers: HEADERS });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      updateSwarmProgress(data);
    } catch (err) {
      console.error("Swarm status poll failed:", err);
    }
  }

  async function stopSwarmRun() {
    try {
      const res = await fetch(`${SWARM_BASE}/stop`, { method: "POST", headers: HEADERS });
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(`${res.status}: ${text}`);
      }
    } catch (err) {
      showError(`Failed to stop swarm test: ${err.message}`);
    }
  }

  function exportSwarmResults() {
    if (!swarmLastData) return;
    const blob = new Blob([JSON.stringify(swarmLastData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `swarm-loadtest-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function startSwarmPolling() {
    stopSwarmPolling();
    pollSwarmStatus();
    swarmPollTimer = setInterval(pollSwarmStatus, POLL_INTERVAL_MS);
  }

  function stopSwarmPolling() {
    if (swarmPollTimer) {
      clearInterval(swarmPollTimer);
      swarmPollTimer = null;
    }
  }

  async function swarmPollOnce() {
    try {
      const res = await fetch(`${SWARM_BASE}/status`, { headers: HEADERS });
      if (!res.ok) return;
      const data = await res.json();
      if (data.status === "running") {
        updateSwarmProgress(data);
        setSwarmRunning(true);
        startSwarmPolling();
      } else if (["complete", "stopped", "error"].includes(data.status)) {
        updateSwarmProgress(data);
      }
    } catch {
      // Silently ignore — server might not be up yet
    }
  }

  swarmStartBtn.addEventListener("click", startSwarmRun);
  swarmStopBtn.addEventListener("click", stopSwarmRun);
  swarmExportBtn.addEventListener("click", exportSwarmResults);
})();
