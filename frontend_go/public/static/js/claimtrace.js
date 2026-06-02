(function () {
  const API_BASE = window.TRIAGE_API_BASE || "";
  const BASE = API_BASE ? `${API_BASE}/claimtrace` : "/claimtrace";
  let lastClaimId = "CLAIM-A";
  let lastBundleId = "";
  let lastStateHash = "state-hash-demo";

  function $(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const el = $(id);
    if (!el) return;
    el.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  async function request(path, options) {
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      ...options,
    });
    const text = await res.text();
    let data = text;
    try { data = text ? JSON.parse(text) : {}; } catch {}
    if (!res.ok) throw new Error(typeof data === "string" ? data : (data.detail || "Claimtrace request failed"));
    return data;
  }

  async function loadSummary() {
    try {
      const data = await request("/summary");
      $("claimtraceSummary").innerHTML = `
        <strong>${data.events}</strong><span>journal events</span>
        <strong>${data.claims}</strong><span>claims</span>
        <strong>${data.bundles}</strong><span>bundles</span>
      `;
    } catch (err) {
      $("claimtraceSummary").textContent = err.message;
    }
  }

  $("ctDeriveClaim")?.addEventListener("click", async () => {
    try {
      const data = await request("/identity/claim", {
        method: "POST",
        body: JSON.stringify({
          submitter_id: $("ctSubmitter").value,
          subscriber_id: $("ctSubscriber").value,
          patient_dob: $("ctDob").value,
          dos_start: $("ctDos").value,
          charge_amount_cents: Number($("ctCharge").value || 0),
          payer_id: $("ctPayer").value,
          line_items: [{ proc_code: "99213", dos: $("ctDos").value, units: 1, charge_amount_cents: Number($("ctCharge").value || 0) }],
        }),
      });
      lastClaimId = data.claim_id;
      $("ctJournalClaim").value = data.claim_id;
      write("ctIdentityOut", data);
    } catch (err) {
      write("ctIdentityOut", err.message);
    }
  });

  $("ctStampX12")?.addEventListener("click", async () => {
    try {
      const traceContext = {
        claim_id: lastClaimId,
        claim_root_id: lastClaimId,
        bundle_id: lastBundleId || null,
        trace_id: `trace-${Date.now()}`,
        prior_state_hash: null,
        new_state_hash: lastStateHash,
        correlation_ids: {},
      };
      const data = await request("/correlation/stamp", {
        method: "POST",
        body: JSON.stringify({ x12_text: $("ctX12").value, trace_context: traceContext }),
      });
      $("ctX12").value = data.x12_text;
      write("ctCorrelationOut", data);
    } catch (err) {
      write("ctCorrelationOut", err.message);
    }
  });

  $("ctExtractX12")?.addEventListener("click", async () => {
    try {
      const data = await request("/correlation/extract", {
        method: "POST",
        body: JSON.stringify({ x12_text: $("ctX12").value }),
      });
      write("ctCorrelationOut", data);
    } catch (err) {
      write("ctCorrelationOut", err.message);
    }
  });

  $("ctAppendEvent")?.addEventListener("click", async () => {
    try {
      const payload = {
        claim_id: $("ctJournalClaim").value || lastClaimId,
        bundle_id: $("ctJournalBundle").value || null,
        operation_type: $("ctJournalOp").value,
        payload_location: $("ctPayloadLocation").value,
        payload: JSON.stringify({ source: "claimtrace-ui", ts: new Date().toISOString() }),
        correlation_ids: {},
      };
      if (payload.bundle_id) lastBundleId = payload.bundle_id;
      if (payload.operation_type === "ADJUDICATE") {
        payload.correlation_ids = { payment_id: $("ctPaymentId").value, trn: $("ctTrn").value };
      }
      const data = await request("/journal/events", { method: "POST", body: JSON.stringify(payload) });
      lastStateHash = data.new_state_hash;
      write("ctJournalOut", data);
      loadSummary();
    } catch (err) {
      write("ctJournalOut", err.message);
    }
  });

  $("ctLoadTrace")?.addEventListener("click", async () => {
    try {
      const claim = encodeURIComponent($("ctJournalClaim").value || lastClaimId);
      const data = await request(`/journal/trace?claim_id=${claim}`);
      write("ctJournalOut", data);
    } catch (err) {
      write("ctJournalOut", err.message);
    }
  });

  $("ctBuildMerkle")?.addEventListener("click", async () => {
    try {
      const claim_hashes = JSON.parse($("ctMerkleInput").value || "{}");
      const data = await request("/merkle/batch", { method: "POST", body: JSON.stringify({ claim_hashes }) });
      write("ctMerkleOut", data);
    } catch (err) {
      write("ctMerkleOut", err.message);
    }
  });

  $("ctPaymentLineage")?.addEventListener("click", async () => {
    try {
      const data = await request(`/lineage/payment/${encodeURIComponent($("ctPaymentId").value)}`);
      write("ctLineageOut", data);
    } catch (err) {
      write("ctLineageOut", err.message);
    }
  });

  $("ct835Lineage")?.addEventListener("click", async () => {
    try {
      const data = await request(`/lineage/835/${encodeURIComponent($("ctTrn").value)}`);
      write("ctLineageOut", data);
    } catch (err) {
      write("ctLineageOut", err.message);
    }
  });

  loadSummary();
})();
