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

  // ── Initial status check ────────────────────────────────────────────────────
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
})();
