const API_BASE = window.HEDI_API_BASE || "";
const INGEST_URL = window.HEDI_INGEST_URL || (API_BASE ? `${API_BASE}/ingest` : "/ingest");
const JOBS_URL = window.HEDI_JOBS_URL || (API_BASE ? `${API_BASE}/jobs` : "/jobs");
const JOBS_DOWNLOAD_BASE = JOBS_URL.replace(/\/$/, "");

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
  if (typeof window.hediLoginURL === "function") {
    window.location.href = window.hediLoginURL(next);
    return;
  }
  const base = (typeof window.HEDI_OAUTH2_START === "string" && window.HEDI_OAUTH2_START.trim()) || "/oauth2/start";
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
  uploadResult.textContent = message;
  uploadResult.dataset.tone = tone;
}

function renderJobs(jobs) {
  if (!jobsBody) return;
  jobsBody.innerHTML = "";
  if (!jobs.length) {
    jobsBody.innerHTML = '<tr><td colspan="6" class="muted">No jobs match your filters yet.</td></tr>';
    return;
  }

  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.dataset.jobId = job.job_id;
    if (job.uploaded_by) {
      tr.dataset.uploadedBy = job.uploaded_by;
    }
    if (job.trading_partner_id) {
      tr.dataset.partnerId = job.trading_partner_id;
    }
    const created = job.created_at ? new Date(job.created_at).toLocaleString() : "";
    tr.innerHTML = `
      <td><code>${job.job_id}</code></td>
      <td>${job.filename}</td>
      <td><span class="status status-${job.status}">${job.status}</span></td>
      <td>${created}</td>
      <td>${job.ack_count ?? 0}</td>
      <td class="actions">
        <button class="ghost" data-detail="${job.job_id}">Details</button>
        <a class="ghost" href="${JOBS_DOWNLOAD_BASE}/${job.job_id}/download" target="_blank" rel="noopener">Download</a>
      </td>`;
    jobsBody.appendChild(tr);
  }
}

async function fetchJobs(uploadedBy, partnerId, silent = false) {
  const params = new URLSearchParams();
  if (uploadedBy) params.set("uploaded_by", uploadedBy);
  if (partnerId) params.set("trading_partner_id", partnerId);
  try {
    const res = await fetch(`${JOBS_URL}?${params.toString()}`);
    if (redirectIfUnauthorized(res)) {
      return;
    }
    if (!res.ok) {
      const msg = await res.text();
      if (!silent) showStatus(`Search failed: ${msg}`, "error");
      return;
    }
    const data = await res.json();
    renderJobs(data);
  } catch (err) {
    console.error(err);
    if (!silent) showStatus("Unable to load jobs — please try again.", "error");
  }
}

async function fetchDetail(jobId) {
  try {
    const res = await fetch(`${JOBS_URL}/${jobId}`);
    if (redirectIfUnauthorized(res)) {
      return;
    }
    if (!res.ok) {
      const msg = await res.text();
      showStatus(`Unable to load job ${jobId}: ${msg}`, "error");
      return;
    }
    const job = await res.json();
    renderDetail(job);
  } catch (err) {
    console.error(err);
    showStatus("Unable to load job detail — please try again.", "error");
  }
}

function renderDetail(job) {
  if (!detailCard || !detailContent) return;
  const created = job.created_at ? new Date(job.created_at).toLocaleString() : "";
  const size = job.file_size ? formatBytes(job.file_size) : "";
  const ackLinks = Array.isArray(job.acknowledgements) && job.acknowledgements.length
    ? job.acknowledgements
        .map(ack => `<li><a href="${JOBS_DOWNLOAD_BASE}/${job.job_id}/acks/${ack.file}" target="_blank" rel="noopener">${ack.label}</a></li>`)
        .join("")
    : "<li class=\"muted\">No acknowledgements generated yet.</li>";
  detailContent.innerHTML = `
    <div class="detail-grid">
      <div>
        <h4>Summary</h4>
        <dl class="meta-grid">
          <div><dt>Job ID</dt><dd><code>${job.job_id}</code></dd></div>
          <div><dt>Status</dt><dd><span class="status status-${job.status}">${job.status}</span></dd></div>
          <div><dt>Submitted</dt><dd>${created}</dd></div>
          <div><dt>Uploaded by</dt><dd>${job.uploaded_by || ""}</dd></div>
          <div><dt>Trading partner</dt><dd>${job.trading_partner_id || ""}</dd></div>
          <div><dt>File size</dt><dd>${size}</dd></div>
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
  const res = await fetch(INGEST_URL, {
    method: "POST",
    body: formData,
  });
  if (redirectIfUnauthorized(res)) {
    return;
  }
  if (!res.ok) {
    const msg = await res.text();
    showStatus(`Upload failed: ${msg}`, "error");
    return;
  }
  const { job_id: jobId } = await res.json();
  showStatus(`Files accepted — tracking job ${jobId}.`, "success");
  await fetchJobs(formData.get("uploaded_by"), formData.get("trading_partner_id"), true);
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
      showStatus("Uploading files…", "info");
      await queueJobs(formData);
      uploadForm.reset();
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
    const uploadedBy = searchUploadedInput ? searchUploadedInput.value.trim() : "";
    const partner = searchPartnerInput ? searchPartnerInput.value.trim() : "";
    await fetchJobs(uploadedBy, partner);
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

if (jobsBody) {
  jobsBody.addEventListener("dblclick", (ev) => {
    const row = ev.target.closest("tr[data-job-id]");
    if (!row) return;
    const jobId = row.getAttribute("data-job-id");
    if (jobId) fetchDetail(jobId);
  });
}

if (detailCard) {
  detailCard.addEventListener("click", (ev) => {
    if (ev.target.matches("button[data-close]") || ev.target.closest("[data-close]") || ev.target === detailCard) {
      detailCard.hidden = true;
    }
  });
}

if (searchUploadedInput) {
  searchUploadedInput.addEventListener("change", () => {
    const uploadedBy = searchUploadedInput.value.trim();
    const partner = searchPartnerInput ? searchPartnerInput.value.trim() : "";
    if (uploadedBy) {
      fetchJobs(uploadedBy, partner, true);
    }
  });
}

if (uploadedByInput && partnerInput) {
  uploadedByInput.addEventListener("change", () => {
    if (!uploadedByInput.value.trim()) {
      return;
    }
    fetchJobs(uploadedByInput.value.trim(), partnerInput.value.trim(), true);
  });
}

window.addEventListener("load", () => {
  if (searchUploadedInput && searchUploadedInput.value) {
    fetchJobs(searchUploadedInput.value.trim(), searchPartnerInput ? searchPartnerInput.value.trim() : "", true);
  }
});
