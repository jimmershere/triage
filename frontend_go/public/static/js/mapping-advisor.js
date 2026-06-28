// Mapping Advisor (Workstream 1) — advisory-only human-approval queue.
// Surfaces ranked mapping suggestions + 999-derived validation rules and lets
// a reviewer approve (creating a versioned rule) or reject them. The frontend
// never applies a mapping; it only calls the advisory API behind the proxy.
(function () {
  const API_BASE = window.TRIAGE_API_BASE || "";
  const BASE = `${API_BASE}/mapping/advisor`;

  function $(id) {
    return document.getElementById(id);
  }

  if (!$("mappingAdvisor")) return; // only run on the Mapping Studio page

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>'"]/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    }[ch]));
  }

  function toast(message, state = "info") {
    const el = $("advisorToast");
    if (!el) return;
    el.hidden = false;
    el.textContent = message;
    el.dataset.state = state;
    window.clearTimeout(toast._t);
    toast._t = window.setTimeout(() => { el.hidden = true; }, 4000);
  }

  async function request(path, options) {
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      ...options,
    });
    const raw = await res.text();
    let data = raw;
    try { data = raw ? JSON.parse(raw) : {}; } catch (_) { /* keep raw */ }
    if (!res.ok) {
      throw new Error(typeof data === "string" ? data : data.detail || "Advisor request failed");
    }
    return data;
  }

  function currentApprover() {
    const profile = window.TRIAGE_PROFILE || window.__triageProfile || {};
    return profile.username || profile.email || "reviewer";
  }

  async function loadSummary() {
    try {
      const s = await request("/summary");
      $("advisorSummary").innerHTML =
        `<strong>${s.pending || 0}</strong> pending · ` +
        `<strong>${s.approved || 0}</strong> approved · ` +
        `<strong>${s.rejected || 0}</strong> rejected · ` +
        `<strong>${s.active_rules || 0}</strong> active rules`;
    } catch (err) {
      $("advisorSummary").textContent = err.message;
    }
  }

  function renderQueue(items) {
    const ul = $("advisorQueue");
    if (!items.length) {
      ul.innerHTML = '<li class="advisor-empty muted">No pending suggestions.</li>';
      return;
    }
    ul.innerHTML = items.map((s) => {
      const pct = Math.round((s.confidence || 0) * 100);
      const kind = s.rule_type === "validation_rule" ? "validation" : "mapping";
      return `
        <li class="advisor-item" data-id="${s.id}">
          <div class="advisor-item-head">
            <span class="advisor-badge advisor-badge--${kind}">${kind}</span>
            <span class="advisor-confidence">${pct}%</span>
          </div>
          <div class="advisor-item-summary">${escapeHtml(s.summary)}</div>
          <div class="advisor-item-meta muted">${escapeHtml(s.transaction_set || "?")} · ${escapeHtml(s.partner_id || "any partner")}</div>
          <div class="advisor-item-actions" data-requires-role="submit">
            <button type="button" class="pill-button pill-button--sm" data-advisor-approve="${s.id}">Approve</button>
            <button type="button" class="pill-button pill-button--sm pill-button--danger" data-advisor-reject="${s.id}">Reject</button>
          </div>
        </li>`;
    }).join("");
  }

  function renderRules(items) {
    const ul = $("advisorRules");
    if (!items.length) {
      ul.innerHTML = '<li class="advisor-empty muted">No active rules yet.</li>';
      return;
    }
    ul.innerHTML = items.map((r) => `
      <li class="advisor-rule">
        <span class="advisor-rule-version">v${r.version}</span>
        <span class="advisor-rule-key">${escapeHtml(r.rule_key)}</span>
        <span class="advisor-rule-by muted">approved by ${escapeHtml(r.approved_by)}</span>
      </li>`).join("");
  }

  async function refresh() {
    await loadSummary();
    try {
      const queue = await request("/suggestions?status=pending");
      renderQueue(queue.suggestions || []);
    } catch (err) {
      $("advisorQueue").innerHTML = `<li class="advisor-empty">${escapeHtml(err.message)}</li>`;
    }
    try {
      const rules = await request("/rules?active_only=true");
      renderRules(rules.rules || []);
    } catch (err) {
      $("advisorRules").innerHTML = `<li class="advisor-empty">${escapeHtml(err.message)}</li>`;
    }
  }

  async function generate() {
    const body = {
      x12_text: $("advisorX12").value.trim(),
      flat_file_text: $("advisorFlat").value.trim() || null,
      delimiter: $("advisorDelimiter").value || null,
      header: $("advisorHeader").checked,
      ack_999_text: $("advisor999").value.trim() || null,
      partner_id: $("advisorPartner").value.trim() || null,
      persist: true,
    };
    if (!body.x12_text) {
      toast("Paste an X12 transaction first.", "error");
      return;
    }
    try {
      const result = await request("/suggest", { method: "POST", body: JSON.stringify(body) });
      const n = (result.enqueued_ids || []).length;
      toast(`Queued ${n} suggestion${n === 1 ? "" : "s"} for review.`, "success");
      await refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function decide(id, decision) {
    try {
      await request(`/suggestions/${id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ approver: currentApprover() }),
      });
      toast(`Suggestion ${decision === "approve" ? "approved" : "rejected"}.`, "success");
      await refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function wire() {
    $("advisorGenerate").addEventListener("click", generate);
    const refreshBtn = $("advisorRefresh");
    if (refreshBtn) refreshBtn.addEventListener("click", refresh);
    $("advisorQueue").addEventListener("click", (ev) => {
      const approve = ev.target.closest("[data-advisor-approve]");
      const reject = ev.target.closest("[data-advisor-reject]");
      if (approve) decide(approve.getAttribute("data-advisor-approve"), "approve");
      else if (reject) decide(reject.getAttribute("data-advisor-reject"), "reject");
    });
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
