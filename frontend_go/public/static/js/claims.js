const API_BASE = window.TRIAGE_API_BASE || "";
const INGEST_URL = window.TRIAGE_INGEST_URL || (API_BASE ? `${API_BASE}/ingest` : "/ingest");
const JOBS_URL = window.TRIAGE_JOBS_URL || (API_BASE ? `${API_BASE}/jobs` : "/jobs");
const JOBS_DOWNLOAD_BASE = JOBS_URL.replace(/\/$/, "");
const CLAIMS_STATE_KEY = "turbohediClaimsFilters";

const uploadForm = document.getElementById("uploadForm");
const uploadResult = document.getElementById("result");
const fileInput = document.getElementById("file");
const uploadedByInput = document.getElementById("uploadedBy");
const partnerInput = document.getElementById("partnerId");
const jobsBody = document.getElementById("jobsBody");
const searchForm = document.getElementById("searchForm");
const searchUploadedInput = document.getElementById("searchUploadedBy");
const searchPartnerInput = document.getElementById("searchPartnerId");
const detailCard = document.getElementById("detailCard");
const detailContent = document.getElementById("detailContent");

function params() {
  return new URLSearchParams(window.location.search || "");
}

function loadState() {
  try {
    return JSON.parse(localStorage.getItem(CLAIMS_STATE_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

function saveState(state) {
  try {
    localStorage.setItem(CLAIMS_STATE_KEY, JSON.stringify(state));
  } catch {}
}

function syncState(patch = {}) {
  saveState({ ...loadState(), ...patch });
}

function formatBytes(size) {
  if (size === undefined || size === null) return "";
  if (size === 0) return "0 bytes";
  const units = ["bytes", "KB", "MB", "GB"];
  const power = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  const value = size / Math.pow(1024, power);
  return `${value.toFixed(power === 0 ? 0 : 1)} ${units[power]}`;
}

function sendToLogin() {
  const next = `${window.location.pathname}${window.location.search || ""}`;
  if (typeof window.triageLoginURL === "function") {
    window.location.href = window.triageLoginURL(next);
    return;
  }
  const base = (typeof window.TRIAGE_OAUTH2_START === "string" && window.TRIAGE_OAUTH2_START.trim()) || "/oauth2/start";
  const separator = base.includes("?") ? "&" : "?";
  window.location.href = `${base}${separator}rd=${encodeURIComponent(next || "/")}`;
}

function redirectIfUnauthorized(res) {
  if (res.status === 401 || res.status === 403) {
    sendToLogin();
    return true;
  }
  return false;
}

function showStatus(message, tone = "info") {
  if (!uploadResult) return;
  uploadResult.hidden = false;
  uploadResult.innerHTML = message;
  uploadResult.dataset.tone = tone;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderJobs(jobs) {
  if (!jobsBody) return;
  jobsBody.innerHTML = "";
  if (!jobs.length) {
    jobsBody.innerHTML = '<tr><td colspan="8" class="muted">No jobs match your filters yet.</td></tr>';
    return;
  }

  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.dataset.jobId = job.job_id;
    tr.innerHTML = `
      <td><code>${job.job_id}</code></td>
      <td>${escapeHtml(job.filename)}</td>
      <td><span class="status status-${escapeHtml(job.status || "pending")}">${escapeHtml(job.status || "pending")}</span></td>
      <td><span class="status status-${escapeHtml(job.validation_status || "pending")}">${escapeHtml(job.validation_status || "pending")}</span></td>
      <td>${job.claims_count ?? "—"}</td>
      <td>${job.ack_count ?? 0}</td>
      <td>${job.created_at ? new Date(job.created_at).toLocaleString() : ""}</td>
      <td class="actions">
        <button class="ghost" data-detail="${job.job_id}">Details</button>
        <a class="ghost" href="/processed.html?uploaded_by=${encodeURIComponent(job.uploaded_by || "")}&trading_partner_id=${encodeURIComponent(job.trading_partner_id || "")}&job_id=${encodeURIComponent(job.job_id)}">Import Detail</a>
      </td>`;
    jobsBody.appendChild(tr);
  }
}

async function fetchJobs(uploadedBy, partnerId, silent = false) {
  const params = new URLSearchParams();
  if (uploadedBy) params.set("uploaded_by", uploadedBy);
  if (partnerId) params.set("trading_partner_id", partnerId);
  syncState({ uploadedBy, tradingPartnerId: partnerId });
  try {
    const res = await fetch(`${JOBS_URL}?${params.toString()}`);
    if (redirectIfUnauthorized(res)) return;
    if (!res.ok) {
      const msg = await res.text();
      if (!silent) showStatus(`Search failed: ${escapeHtml(msg)}`, "error");
      return;
    }
    renderJobs(await res.json());
  } catch (err) {
    console.error(err);
    if (!silent) showStatus("Unable to load jobs — please try again.", "error");
  }
}

async function fetchDetail(jobId) {
  try {
    const res = await fetch(`${JOBS_URL}/${jobId}`);
    if (redirectIfUnauthorized(res)) return;
    if (!res.ok) {
      showStatus(`Unable to load job ${escapeHtml(jobId)}: ${escapeHtml(await res.text())}`, "error");
      return;
    }
    renderDetail(await res.json());
  } catch (err) {
    console.error(err);
    showStatus("Unable to load job detail — please try again.", "error");
  }
}

function renderDetail(job) {
  if (!detailCard || !detailContent) return;
  const created = job.created_at ? new Date(job.created_at).toLocaleString() : "—";
  const processed = job.processed_at ? new Date(job.processed_at).toLocaleString() : "—";
  const ackLinks = Array.isArray(job.acknowledgements) && job.acknowledgements.length
    ? job.acknowledgements
        .map((ack) => `<li><a href="${JOBS_DOWNLOAD_BASE}/${job.job_id}/acks/${ack.id}/download" target="_blank" rel="noopener">${escapeHtml(ack.ack_type)} acknowledgement</a></li>`)
        .join("")
    : '<li class="muted">No acknowledgements generated yet.</li>';
  detailContent.innerHTML = `
    <div class="detail-grid">
      <div>
        <h4>Summary</h4>
        <dl class="meta-grid">
          <div><dt>Job ID</dt><dd><code>${job.job_id}</code></dd></div>
          <div><dt>Status</dt><dd><span class="status status-${escapeHtml(job.status || "pending")}">${escapeHtml(job.status || "pending")}</span></dd></div>
          <div><dt>Validation</dt><dd><span class="status status-${escapeHtml(job.validation_status || "pending")}">${escapeHtml(job.validation_status || "pending")}</span></dd></div>
          <div><dt>Claims</dt><dd>${job.claims_count ?? "—"}</dd></div>
          <div><dt>Uploaded</dt><dd>${created}</dd></div>
          <div><dt>Processed</dt><dd>${processed}</dd></div>
          <div><dt>Uploaded by</dt><dd>${escapeHtml(job.uploaded_by || "—")}</dd></div>
          <div><dt>Trading partner</dt><dd>${escapeHtml(job.trading_partner_id || "—")}</dd></div>
        </dl>
      </div>
      <div>
        <h4>Downloads</h4>
        <ul class="download-list">
          <li><a href="${JOBS_DOWNLOAD_BASE}/${job.job_id}/download" target="_blank" rel="noopener">Original upload</a></li>
          ${ackLinks}
        </ul>
      </div>
    </div>`;
  detailCard.hidden = false;
  detailCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function queueJobs(formData) {
  const uploadedBy = String(formData.get("uploaded_by") || "").trim();
  const tradingPartnerId = String(formData.get("trading_partner_id") || "").trim();
  const res = await fetch(INGEST_URL, { method: "POST", body: formData });
  if (redirectIfUnauthorized(res)) return;
  if (!res.ok) {
    showStatus(`Upload failed: ${escapeHtml(await res.text())}`, "error");
    return;
  }
  const { job_id: jobId, filename, queued_bytes: queuedBytes } = await res.json();
  syncState({ uploadedBy, tradingPartnerId, lastJobId: jobId });
  const detailHref = `/processed.html?uploaded_by=${encodeURIComponent(uploadedBy)}&trading_partner_id=${encodeURIComponent(tradingPartnerId)}&job_id=${encodeURIComponent(jobId)}`;
  showStatus(
    `File accepted — tracking <strong>${escapeHtml(jobId)}</strong> (${escapeHtml(filename || "upload")}, ${escapeHtml(formatBytes(queuedBytes))}). <a href="${detailHref}">Open Import Detail</a>.`,
    "success"
  );
  await fetchJobs(uploadedBy, tradingPartnerId, true);
}

function hydrateInputs() {
  const state = loadState();
  const q = params();
  const uploadedBy = q.get("uploaded_by") || state.uploadedBy || "";
  const partner = q.get("trading_partner_id") || state.tradingPartnerId || "";
  if (uploadedByInput && !uploadedByInput.value) uploadedByInput.value = uploadedBy;
  if (partnerInput && !partnerInput.value) partnerInput.value = partner;
  if (searchUploadedInput && !searchUploadedInput.value) searchUploadedInput.value = uploadedBy;
  if (searchPartnerInput && !searchPartnerInput.value) searchPartnerInput.value = partner;
}

if (uploadForm) {
  uploadForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!fileInput || !fileInput.files || !fileInput.files.length) {
      showStatus("Pick at least one file to upload.", "error");
      return;
    }
    const formData = new FormData(uploadForm);
    try {
      showStatus(`Uploading ${fileInput.files.length} file(s)…`, "info");
      await queueJobs(formData);
      uploadForm.reset();
      hydrateInputs();
      if (uploadedByInput) uploadedByInput.focus();
    } catch (err) {
      console.error(err);
      showStatus("Upload failed — please try again.", "error");
    }
  });
}

if (searchForm) {
  searchForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    await fetchJobs(searchUploadedInput ? searchUploadedInput.value.trim() : "", searchPartnerInput ? searchPartnerInput.value.trim() : "");
  });
}

if (jobsBody) {
  jobsBody.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-detail]");
    if (!btn) return;
    const jobId = btn.getAttribute("data-detail");
    if (jobId) fetchDetail(jobId);
  });
}

hydrateInputs();
if (searchUploadedInput && searchUploadedInput.value.trim()) {
  fetchJobs(searchUploadedInput.value.trim(), searchPartnerInput ? searchPartnerInput.value.trim() : "", true);
}
