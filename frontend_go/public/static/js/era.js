(function () {
  const API_BASE = window.TRIAGE_API_BASE || "";
  const BASE = API_BASE ? `${API_BASE}/era` : "/era";
  let selected = null;

  function $(id) { return document.getElementById(id); }
  function text(v, f = "—") { return v === null || v === undefined || v === "" ? f : String(v); }
  function escapeHtml(v) {
    return text(v, "").replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c]));
  }
  function setStatus(el, msg, state = "info") {
    if (!el) return;
    el.hidden = false; el.textContent = msg; el.dataset.state = state;
  }

  async function request(path, options) {
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      ...options,
    });
    const raw = await res.text();
    let data = raw;
    try { data = raw ? JSON.parse(raw) : {}; } catch {}
    if (!res.ok) throw new Error(typeof data === "string" ? data : (data.detail || "ERA request failed"));
    return data;
  }

  function renderSummary(d) {
    const el = $("eraSummary");
    if (!el) return;
    el.innerHTML = `
      <strong>${d.artifacts || 0}</strong><span>stored 835s</span>
      <strong>${d.reconstructed || 0}</strong><span>reconstructions</span>
      <strong>${d.reversals || 0}</strong><span>reversals</span>
      <strong>${d.unbalanced || 0}</strong><span>unbalanced</span>`;
  }

  async function loadSummary() {
    try { renderSummary(await request("/summary")); }
    catch (err) { const el = $("eraSummary"); if (el) el.textContent = err.message; }
  }

  function searchQuery() {
    const p = new URLSearchParams();
    const trn = $("eraTrn")?.value?.trim();
    const claim = $("eraClaimId")?.value?.trim();
    const origin = $("eraOrigin")?.value?.trim();
    if (trn) p.set("trn", trn);
    if (claim) p.set("claim_id", claim);
    if (origin) p.set("origin", origin);
    p.set("limit", "100");
    return p.toString();
  }

  function renderRows(rows) {
    const body = $("eraResultsBody");
    $("eraResultCount").textContent = rows.length;
    if (!rows.length) { body.innerHTML = `<tr><td colspan="6" class="muted">No artifacts found.</td></tr>`; return; }
    body.innerHTML = rows.map((r) => `
      <tr data-id="${escapeHtml(r.artifact_id)}" class="claimtrace-row">
        <td>${escapeHtml(r.trn)}</td>
        <td>${escapeHtml(r.claim_id)}</td>
        <td><span class="badge">${escapeHtml(r.origin)}</span></td>
        <td>${escapeHtml(r.bpr_amount)}</td>
        <td>${r.balanced ? "✓" : "✗"}</td>
        <td>${escapeHtml((r.created_at || "").slice(0, 19))}</td>
      </tr>`).join("");
    body.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => loadDetail(tr.getAttribute("data-id")));
    });
  }

  async function loadList() {
    const body = $("eraResultsBody");
    body.innerHTML = `<tr><td colspan="6" class="muted">Loading artifacts…</td></tr>`;
    try { renderRows((await request(`/artifacts?${searchQuery()}`)).artifacts || []); }
    catch (err) { body.innerHTML = `<tr><td colspan="6" class="muted">${escapeHtml(err.message)}</td></tr>`; }
  }

  function renderDetail(a) {
    $("eraDetailEmpty").hidden = true;
    $("eraDetailContent").hidden = false;
    $("eraDetailTitle").textContent = `TRN ${a.trn || "(none)"}`;
    $("eraDetailStatus").textContent = `${a.origin} · ${a.balanced ? "balanced" : "UNBALANCED"}`;
    $("eraIdentityGrid").innerHTML = [
      ["Artifact", a.artifact_id], ["Claim", a.claim_id], ["TRN", a.trn],
      ["Direction", a.direction], ["Origin", a.origin], ["Payer", a.payer_id],
      ["Payee", a.payee_id], ["BPR amount", a.bpr_amount], ["SHA-256", a.content_sha256],
    ].map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("");
    const report = (a.balance_report && a.balance_report.errors) || [];
    $("eraBalance").innerHTML = a.balanced
      ? `<li>All three balancing relationships hold (SVC, CLP, BPR).</li>`
      : report.map((e) => `<li><strong>${escapeHtml(e.code)}</strong>: ${escapeHtml(e.message)}</li>`).join("") || `<li>Unbalanced.</li>`;
    $("eraOutput").textContent = a.raw_835_text || "";
  }

  async function loadDetail(id) {
    selected = id;
    try { renderDetail(await request(`/artifacts/${encodeURIComponent(id)}`)); }
    catch (err) { setStatus($("eraActionStatus"), err.message, "error"); }
  }

  async function act(kind) {
    if (!selected) return;
    const status = $("eraActionStatus");
    try {
      if (kind === "redeliver") {
        const r = await request(`/artifacts/${encodeURIComponent(selected)}/redeliver`, { method: "POST", body: "{}" });
        $("eraOutput").textContent = r.raw_835_text || "";
        setStatus(status, `Re-delivered byte-for-byte (TRN ${r.trn}).`, "success");
      } else if (kind === "reconstruct") {
        const r = await request(`/artifacts/${encodeURIComponent(selected)}/reconstruct`, { method: "POST", body: "{}" });
        $("eraOutput").textContent = r.x12 || "";
        setStatus(status, `Reconstructed (balanced=${r.balanced}). New artifact ${r.artifact.artifact_id}.`, r.balanced ? "success" : "error");
        loadList(); loadSummary();
      } else if (kind === "reverse") {
        const r = await request(`/artifacts/${encodeURIComponent(selected)}/reverse`, { method: "POST", body: "{}" });
        $("eraOutput").textContent = r.x12 || "";
        setStatus(status, `Reversal modelled (CLP02=22, balanced=${r.balanced}). New artifact ${r.artifact.artifact_id}.`, "success");
        loadList(); loadSummary();
      }
    } catch (err) { setStatus(status, err.message, "error"); }
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadSummary(); loadList();
    $("eraRefresh")?.addEventListener("click", () => { loadSummary(); loadList(); });
    $("eraSearchForm")?.addEventListener("submit", (e) => { e.preventDefault(); loadList(); });
    $("eraClear")?.addEventListener("click", () => { $("eraTrn").value = ""; $("eraClaimId").value = ""; $("eraOrigin").value = ""; loadList(); });
    $("eraRedeliver")?.addEventListener("click", () => act("redeliver"));
    $("eraReconstruct")?.addEventListener("click", () => act("reconstruct"));
    $("eraReverse")?.addEventListener("click", () => act("reverse"));
  });
})();
