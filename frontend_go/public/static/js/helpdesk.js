(function () {
  const API_BASE = window.TRIAGE_API_BASE || "";
  const BASE = API_BASE ? `${API_BASE}/helpdesk` : "/helpdesk";
  let selected = null;

  function $(id) { return document.getElementById(id); }
  function text(v, f = "—") { return v === null || v === undefined || v === "" ? f : String(v); }
  function escapeHtml(v) {
    return text(v, "").replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c]));
  }
  function setStatus(el, msg, state = "info") { if (!el) return; el.hidden = false; el.textContent = msg; el.dataset.state = state; }

  async function request(path, options) {
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      ...options,
    });
    const raw = await res.text();
    let data = raw;
    try { data = raw ? JSON.parse(raw) : {}; } catch {}
    if (!res.ok) throw new Error(typeof data === "string" ? data : (data.detail || "Helpdesk request failed"));
    return data;
  }

  function renderSummary(d) {
    const el = $("hdSummary");
    if (!el) return;
    el.innerHTML = `
      <strong>${d.open || 0}</strong><span>open</span>
      <strong>${d.submitter_notified || 0}</strong><span>notified</span>
      <strong>${d.awaiting_resubmission || 0}</strong><span>awaiting</span>
      <strong>${d.resolved || 0}</strong><span>resolved</span>`;
  }

  async function loadSummary() {
    try { renderSummary(await request("/summary")); }
    catch (err) { const el = $("hdSummary"); if (el) el.textContent = err.message; }
  }

  function locationOf(c) {
    const bits = [];
    if (c.loop_id) bits.push(`loop ${c.loop_id}`);
    if (c.segment_id) bits.push(`${c.segment_id}${c.element_position || ""}`);
    return bits.join(" · ") || "—";
  }

  function renderRows(rows) {
    const body = $("hdResultsBody");
    $("hdResultCount").textContent = rows.length;
    if (!rows.length) { body.innerHTML = `<tr><td colspan="6" class="muted">No cases.</td></tr>`; return; }
    body.innerHTML = rows.map((c) => `
      <tr data-id="${escapeHtml(c.case_id)}" class="claimtrace-row">
        <td>${escapeHtml(c.case_number)}</td>
        <td>${escapeHtml(c.claim_id)}</td>
        <td>${escapeHtml(c.trading_partner_id)}</td>
        <td>${escapeHtml(c.reason_code)}</td>
        <td>${escapeHtml(locationOf(c))}</td>
        <td><span class="badge">${escapeHtml(c.status)}</span></td>
      </tr>`).join("");
    body.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => loadDetail(tr.getAttribute("data-id")));
    });
  }

  async function loadList() {
    const body = $("hdResultsBody");
    body.innerHTML = `<tr><td colspan="6" class="muted">Loading cases…</td></tr>`;
    const status = $("hdStatus")?.value?.trim();
    const partner = $("hdPartner")?.value?.trim();
    const claim = $("hdClaim")?.value?.trim();
    try {
      if (!status && !partner && !claim) {
        renderRows((await request("/queue?limit=200")).queue || []);
        return;
      }
      const p = new URLSearchParams();
      if (status) p.set("status", status);
      if (partner) p.set("trading_partner_id", partner);
      if (claim) p.set("claim_id", claim);
      p.set("limit", "200");
      renderRows((await request(`/cases?${p.toString()}`)).cases || []);
    } catch (err) { body.innerHTML = `<tr><td colspan="6" class="muted">${escapeHtml(err.message)}</td></tr>`; }
  }

  function renderDetail(detail) {
    const c = detail.case;
    $("hdDetailEmpty").hidden = true;
    $("hdDetailContent").hidden = false;
    $("hdDetailTitle").textContent = c.case_number;
    $("hdDetailStatus").textContent = c.status;
    $("hdIdentityGrid").innerHTML = [
      ["Claim", c.claim_id], ["Partner", c.trading_partner_id], ["Submitter", c.submitter_id],
      ["Ack type", c.ack_type], ["Reason", `${text(c.reason_code)} ${text(c.reason_text, "")}`],
      ["Location", locationOf(c)], ["Resubmission", c.resubmission_claim_id], ["Created", (c.created_at || "").slice(0, 19)],
    ].map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("");
    $("hdTimeline").innerHTML = (detail.events || []).map((e) => `
      <li><strong>${escapeHtml(e.event_type)}</strong> ${escapeHtml((e.from_status) || "")}${e.to_status ? " → " + escapeHtml(e.to_status) : ""}
      <span class="muted">${escapeHtml((e.ts || "").slice(0, 19))}</span></li>`).join("");
  }

  async function loadDetail(id) {
    selected = id;
    try { renderDetail(await request(`/cases/${encodeURIComponent(id)}`)); }
    catch (err) { setStatus($("hdActionStatus"), err.message, "error"); }
  }

  async function notify() {
    if (!selected) return;
    try {
      const r = await request(`/cases/${encodeURIComponent(selected)}/notify`, { method: "POST", body: "{}" });
      $("hdReport").textContent = (r.report && r.report.text) || "";
      setStatus($("hdActionStatus"), "Submitter notified; report packaged.", "success");
      loadDetail(selected); loadSummary(); loadList();
    } catch (err) { setStatus($("hdActionStatus"), err.message, "error"); }
  }

  async function setStatusTo(target) {
    if (!selected) return;
    try {
      await request(`/cases/${encodeURIComponent(selected)}/status`, {
        method: "POST", body: JSON.stringify({ status: target }),
      });
      setStatus($("hdActionStatus"), `Status → ${target}.`, "success");
      loadDetail(selected); loadSummary(); loadList();
    } catch (err) { setStatus($("hdActionStatus"), err.message, "error"); }
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadSummary(); loadList();
    $("hdRefresh")?.addEventListener("click", () => { loadSummary(); loadList(); });
    $("hdSearchForm")?.addEventListener("submit", (e) => { e.preventDefault(); loadList(); });
    $("hdClear")?.addEventListener("click", () => { $("hdStatus").value = ""; $("hdPartner").value = ""; $("hdClaim").value = ""; loadList(); });
    $("hdNotify")?.addEventListener("click", notify);
    $("hdAwaiting")?.addEventListener("click", () => setStatusTo("awaiting-resubmission"));
    $("hdResolve")?.addEventListener("click", () => setStatusTo("resolved"));
  });
})();
