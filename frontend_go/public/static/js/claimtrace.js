(function () {
  const API_BASE = window.TRIAGE_API_BASE || "";
  const BASE = API_BASE ? `${API_BASE}/claimtrace` : "/claimtrace";
  let selectedClaimKey = null;

  function $(id) {
    return document.getElementById(id);
  }

  function text(value, fallback = "—") {
    if (value === null || value === undefined || value === "") return fallback;
    return String(value);
  }

  function short(value, size = 12) {
    const v = text(value, "");
    return v.length > size ? `${v.slice(0, size)}…` : v || "—";
  }

  function escapeHtml(value) {
    return text(value, "").replace(/[&<>'"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[ch]));
  }

  function setStatus(el, message, state = "info") {
    if (!el) return;
    el.hidden = false;
    el.textContent = message;
    el.dataset.state = state;
  }

  async function request(path, options) {
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      ...options,
    });
    const raw = await res.text();
    let data = raw;
    try { data = raw ? JSON.parse(raw) : {}; } catch {}
    if (!res.ok) throw new Error(typeof data === "string" ? data : (data.detail || "Claimtrace request failed"));
    return data;
  }

  function renderSummary(data) {
    const el = $("claimtraceSummary");
    if (!el) return;
    el.innerHTML = `
      <strong>${data.claims || 0}</strong><span>tracked files</span>
      <strong>${data.events || 0}</strong><span>trace events</span>
      <strong>${data.repair_count || 0}</strong><span>repair marks</span>
      <strong>${data.delete_count || 0}</strong><span>deletion marks</span>
    `;
  }

  async function loadSummary() {
    try {
      renderSummary(await request("/summary"));
    } catch (err) {
      const el = $("claimtraceSummary");
      if (el) el.textContent = err.message;
    }
  }

  function searchParams() {
    const params = new URLSearchParams();
    const values = {
      trading_partner_id: $("ctTradingPartner")?.value,
      submitter_id: $("ctSubmitter")?.value,
      claim_id: $("ctClaimId")?.value,
      claim_hash_id: $("ctClaimHash")?.value,
      action_state: $("ctActionState")?.value,
    };
    Object.entries(values).forEach(([key, value]) => {
      if (value && value.trim()) params.set(key, value.trim());
    });
    params.set("limit", "100");
    return params.toString();
  }

  function actionLabel(action) {
    switch (action) {
      case "repair": return "Repair";
      case "delete": return "Delete";
      case "reviewed": return "Reviewed";
      default: return "Tracked";
    }
  }

  function renderResults(data) {
    const body = $("ctResultsBody");
    const count = $("ctResultCount");
    if (!body) return;
    const claims = data.claims || [];
    if (count) count.textContent = String(data.count || claims.length);
    if (!claims.length) {
      body.innerHTML = '<tr><td colspan="7" class="muted">No claims matched those keys.</td></tr>';
      return;
    }
    body.innerHTML = claims.map((claim) => `
      <tr class="claimtrace-result-row" data-claim-key="${escapeHtml(claim.claim_id)}">
        <td><button type="button" class="claimtrace-row-button" title="${escapeHtml(claim.claim_id)}">${escapeHtml(short(claim.claim_id, 16))}</button></td>
        <td><code title="${escapeHtml(claim.claim_hash_id)}">${escapeHtml(short(claim.claim_hash_id, 14))}</code></td>
        <td>${escapeHtml(claim.trading_partner_id || "—")}</td>
        <td>${escapeHtml(claim.submitter_id || "—")}</td>
        <td><span class="claimtrace-status-pill">${escapeHtml(claim.status || "tracked")}</span></td>
        <td><span class="claimtrace-action-state claimtrace-action-state--${escapeHtml(claim.action_state || "none")}">${escapeHtml(actionLabel(claim.action_state))}</span></td>
        <td>${escapeHtml((claim.created_at || "").slice(0, 19).replace("T", " "))}</td>
      </tr>
    `).join("");
    body.querySelectorAll("[data-claim-key]").forEach((row) => {
      row.addEventListener("click", () => loadDetail(row.dataset.claimKey));
    });
  }

  async function searchClaims() {
    try {
      const query = searchParams();
      renderResults(await request(`/claims?${query}`));
    } catch (err) {
      const body = $("ctResultsBody");
      if (body) body.innerHTML = `<tr><td colspan="7" class="muted">${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderIdentity(claim) {
    const fields = [
      ["Claim ID", claim.claim_id],
      ["Claim hash ID", claim.claim_hash_id],
      ["Tracking ID", claim.tracking_id],
      ["Job ID", claim.job_id],
      ["Import ID", claim.import_id],
      ["Filename", claim.filename],
      ["Trading partner", claim.trading_partner_id],
      ["Submitter", claim.submitter_id],
      ["Uploaded by", claim.uploaded_by],
      ["Trace ID", claim.trace_id],
      ["State hash", claim.state_hash],
      ["Updated", claim.updated_at],
    ];
    const grid = $("ctIdentityGrid");
    if (!grid) return;
    grid.innerHTML = fields.map(([label, value]) => `
      <div><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(short(value, 28))}</strong></div>
    `).join("");
  }

  function renderTimeline(events) {
    const el = $("ctTimeline");
    if (!el) return;
    if (!events.length) {
      el.innerHTML = '<li class="muted">No trace events recorded.</li>';
      return;
    }
    el.innerHTML = events.map((event) => `
      <li>
        <div class="claimtrace-timeline-op">${escapeHtml(event.operation_type)}</div>
        <div class="claimtrace-timeline-meta">${escapeHtml((event.ts || "").slice(0, 19).replace("T", " "))} · ${escapeHtml(event.service_name || "service")}</div>
        <code title="${escapeHtml(event.state_hash)}">${escapeHtml(short(event.state_hash, 24))}</code>
        <div class="muted">${escapeHtml(event.payload_location || "")}</div>
      </li>
    `).join("");
  }

  function renderRecommendations(analysis) {
    const el = $("ctRecommendations");
    if (!el) return;
    const steps = analysis.recommended_next_steps || [];
    el.innerHTML = steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("") || '<li>No recommendations yet.</li>';
  }

  async function loadDetail(claimKey) {
    if (!claimKey) return;
    selectedClaimKey = claimKey;
    const status = $("ctActionStatus");
    if (status) status.hidden = true;
    try {
      const detail = await request(`/claims/${encodeURIComponent(claimKey)}`);
      const claim = detail.claim;
      $("ctDetailEmpty").hidden = true;
      $("ctDetailContent").hidden = false;
      $("ctDetailTitle").textContent = short(claim.claim_id, 22);
      $("ctDetailStatus").textContent = `${claim.status || "tracked"} · ${actionLabel(claim.action_state)}`;
      renderIdentity(claim);
      renderRecommendations(detail.analysis || {});
      renderTimeline(detail.events || []);
    } catch (err) {
      setStatus(status, err.message, "error");
    }
  }

  async function markSelected(action) {
    if (!selectedClaimKey) {
      setStatus($("ctActionStatus"), "Select a claim first.", "error");
      return;
    }
    try {
      const note = $("ctActionNote")?.value || "";
      const result = await request(`/claims/${encodeURIComponent(selectedClaimKey)}/action`, {
        method: "POST",
        body: JSON.stringify({ action, note }),
      });
      setStatus($("ctActionStatus"), `Claim marked: ${actionLabel(result.claim.action_state)}`, "success");
      await loadDetail(result.claim.claim_id);
      await searchClaims();
      await loadSummary();
    } catch (err) {
      setStatus($("ctActionStatus"), err.message, "error");
    }
  }

  function clearSearch() {
    ["ctTradingPartner", "ctSubmitter", "ctClaimId", "ctClaimHash"].forEach((id) => { const el = $(id); if (el) el.value = ""; });
    const action = $("ctActionState");
    if (action) action.value = "";
    searchClaims();
  }

  $("ctSearchForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    searchClaims();
  });
  $("ctRefresh")?.addEventListener("click", () => { loadSummary(); searchClaims(); if (selectedClaimKey) loadDetail(selectedClaimKey); });
  $("ctClearSearch")?.addEventListener("click", clearSearch);
  $("ctMarkRepair")?.addEventListener("click", () => markSelected("repair"));
  $("ctMarkDelete")?.addEventListener("click", () => markSelected("delete"));
  $("ctMarkReviewed")?.addEventListener("click", () => markSelected("reviewed"));
  $("ctClearMark")?.addEventListener("click", () => markSelected("clear"));

  loadSummary();
  searchClaims();
})();
