(function () {
  const DAY = Math.floor(Date.now() / 86400000);
  const DAILY = DAY % 7;
  const path = window.location.pathname.replace(/\/index\.html$/, "/") || "/";

  const pageTips = {
    "/": [
      "Start with dollars at risk, then drill into the exception queue before reviewing lower-risk imports.",
      "Use the Operator, Analyst, and Executive views to shift between queue work and leadership summaries.",
      "Critical validation failures should be paired with partner IDs so repeat sender issues are visible.",
      "Watch queue depth and average processing time together; rising values in both usually signal a downstream bottleneck.",
      "Treat rejected supervisor verdicts as priority evidence packets, not just failed files.",
      "Use recent imports to confirm whether a partner issue is new or part of a developing pattern.",
      "Review alert thresholds weekly so the dashboard continues matching operational risk tolerance."
    ],
    "/processed.html": [
      "Search by operator and trading partner first when reconstructing a customer-facing incident.",
      "Open Validation Issues before Raw X12 when triaging rejects; it gives the fastest path to root cause.",
      "Download acknowledgement bundles when sharing evidence with clearinghouses or payer teams.",
      "Compare parsed JSON and CMS Projection to separate parser errors from business-rule failures.",
      "Use the audit trail as the source of truth for who touched an import and when.",
      "If raw X12 is unexpectedly short, verify the original upload and partner transport logs.",
      "Routing & AI details are most useful when paired with the validation status and claim count."
    ],
    "/ingest.html": [
      "Attach the correct trading partner ID at upload time so downstream Claimtrace search works immediately.",
      "Use small known-good X12 files to validate a new partner profile before bulk uploads.",
      "If a file queues but does not process, check RabbitMQ and worker health before retrying.",
      "Keep original filenames meaningful; they become part of operational search and support workflows.",
      "For demos, upload one clean file and one intentionally flawed file to show routing differences.",
      "When testing 837 files, include realistic ISA/GS submitter IDs for better traceability.",
      "Record who uploaded each batch so audit and customer-support follow-up are straightforward."
    ],
    "/claimtrace.html": [
      "Search by trading partner to see every file Triage has automatically enrolled into Claimtrace.",
      "Use claim hash ID when you need payload-level identity that survives filename changes.",
      "Open Trace Detail before marking a claim for repair or deletion so the action has supporting evidence.",
      "Repair marks should include notes about the suspected segment, loop, or partner rule.",
      "Deletion marks should be reserved for claims requiring supervisor review or removal from downstream handling.",
      "The recommendations panel highlights missing validation, integrity, or lineage milestones.",
      "Use action-state filters to run daily review queues for repair, deletion, and completed reviews."
    ],
    "/portal.html": [
      "Use the portal overview to choose the transaction family before opening a specialized workspace.",
      "Pair eligibility and status transactions with related 837 claims for a complete account story.",
      "Keep portal navigation focused on the claim lifecycle: submit, acknowledge, status, remit.",
      "Use Claimtrace when a claim crosses multiple transaction types or partner handoffs.",
      "Document partner-specific quirks near the transaction type operators use most often.",
      "Executive users should start with summary panels, then drill into exception evidence only as needed.",
      "For training, walk a single claim from 837 submission through 835 remittance."
    ],
    "/claim-entry.html": [
      "Validate demographics and subscriber identifiers before adding service lines.",
      "Use realistic diagnosis and procedure relationships to reduce avoidable payer edits.",
      "Confirm place of service and claim frequency before submission.",
      "Keep notes concise and operational; they may become part of audit context.",
      "Use Claimtrace after submission to confirm the generated claim entered the evidence trail.",
      "Review payer-specific requirements before entering unusual modifiers or high-dollar services.",
      "Save drafts only when required data is missing; completed claims should move quickly into validation."
    ],
    "/edits.html": [
      "Resolve structural X12 issues before business-rule edits; syntax problems can hide downstream defects.",
      "Use the highlighted segment context to understand whether the problem is data, mapping, or partner rules.",
      "Keep repair notes tied to segment IDs and loop names so reviewers can reproduce the fix.",
      "Validate after each meaningful edit instead of stacking many unverified changes.",
      "Treat repeated edits for one partner as a candidate for a partner-profile rule.",
      "When in doubt, compare edited output against a known-good sample from the same transaction type.",
      "Escalate edits that change payment-critical values such as charge amount, subscriber, or diagnosis."
    ],
    "/mapping.html": [
      "Start mapping with envelope and transaction identifiers before detailed claim loops.",
      "Use sample payloads from the same partner to avoid overfitting to generic examples.",
      "Map required loops first, then add optional loops based on partner needs.",
      "Preview generated output after each major mapping group.",
      "Document assumptions for payer-specific transformations directly in the mapping workflow.",
      "Use visual grouping to separate source extraction, normalization, and target X12 construction.",
      "Regression-test mappings with both clean and edge-case claim files."
    ],
    "/partners.html": [
      "Keep partner identifiers consistent with ISA/GS values to improve search and traceability.",
      "Review partner profiles when validation failures cluster around one sender.",
      "Document payer-specific acknowledgement expectations and response timing.",
      "Use partner notes to capture enrollment status, companion guides, and exceptions.",
      "Audit profile changes before bulk production submissions.",
      "Prioritize partners by claim volume and dollars at risk when tuning rules.",
      "Sync partner updates with Claimtrace searches to verify new files are labeled correctly."
    ],
    "/admin.html": [
      "Grant submit privileges only to users who need to upload or edit claim files.",
      "Keep administrator accounts limited and reviewed regularly.",
      "Use support tickets to identify repeat training or configuration issues.",
      "Confirm authentication-provider toggles before changing production access patterns.",
      "After role changes, ask users to refresh so client-side controls reflect new permissions.",
      "Review bootstrap credentials and rotate them before production deployment.",
      "Use role management as an operational control, not a substitute for audit review."
    ],
    "/about.html": [
      "Use this page to orient new users around Triage’s operational claim-processing model.",
      "Frame demos around intake, validation, routing, traceability, and payer response.",
      "Explain that X12 evidence and business decisions are linked through the same dashboard workflow.",
      "Point executives to outcomes and risk, then analysts to trace-level detail.",
      "Use the visual flow to explain how files move from upload to acknowledgement.",
      "Highlight Claimtrace as the continuity layer across transaction types.",
      "Keep reference material concise so operators can return quickly to work queues."
    ],
    "/edi-news.html": [
      "Use reference notes to keep operators aligned on transaction standards and payer terminology.",
      "When standards change, check mappings, validation rules, and partner profiles together.",
      "Tie each reference item back to a workflow impact so it is actionable.",
      "Prioritize notes that reduce repeat support tickets or claim rework.",
      "Use examples from real X12 segments when documenting abstract rules.",
      "Review 835 and 277 notes alongside 837 submission rules for end-to-end clarity.",
      "Archive stale guidance so operators do not follow outdated companion-guide assumptions."
    ],
    "/login.html": [
      "Use an account with the minimum role needed for the task you are performing.",
      "Administrators should sign out after user-management work is complete.",
      "If login redirects unexpectedly, verify the configured OAuth start path.",
      "Use strong, rotated credentials for bootstrap and demo accounts.",
      "Portal users can review claims without needing administrator access.",
      "Submitter access should be reserved for upload and edit workflows.",
      "Report repeated login failures so identity-provider configuration can be reviewed."
    ]
  };

  const portalTips = [
    "Confirm transaction type and companion guide before interpreting segment-level results.",
    "Use Claimtrace when a transaction needs to be connected back to an originating claim.",
    "Pair portal views with Import Detail for raw payload evidence.",
    "Check acknowledgements before assuming a payer accepted or rejected a claim.",
    "Keep transaction examples realistic so analysts can spot partner-specific differences.",
    "Review status and remittance files alongside the submitted 837 for a complete lifecycle.",
    "Escalate unusual control-number mismatches because they can break reconciliation."
  ];

  const pageInfo = {
    "/": ["Command center insight", "Operational leaders should monitor throughput, risk exposure, and exception queues in one pass."],
    "/processed.html": ["Import detail insight", "Use file-level evidence to connect validation, acknowledgements, and audit history."],
    "/ingest.html": ["Ingest insight", "Accurate partner and submitter context at upload time improves every downstream workflow."],
    "/claimtrace.html": ["Claimtrace insight", "Searchable identity, events, and actions give every ingested claim an operational evidence trail."],
    "/portal.html": ["Claims portal insight", "Transaction-specific views help operators move from submission through status and payment."],
    "/claim-entry.html": ["Claim entry insight", "Clean front-door data reduces avoidable validation failures and repair cycles."],
    "/edits.html": ["Edits insight", "Segment-aware repair workflows help analysts fix claims without losing audit context."],
    "/mapping.html": ["Mapping insight", "Reliable maps turn partner-specific payloads into consistent operational data."],
    "/partners.html": ["Partner insight", "Partner profiles are operational controls for routing, validation, and support."],
    "/admin.html": ["Administration insight", "Role and identity governance keeps operational access aligned with responsibility."],
    "/about.html": ["Platform insight", "Triage connects EDI intake, validation, routing, and traceability for claim operations."],
    "/edi-news.html": ["Reference insight", "Standards guidance is most valuable when it maps directly to operator decisions."],
    "/login.html": ["Access insight", "Authentication and roles protect both claim data and operational controls."]
  };

  function normalizedPath() {
    if (path.startsWith("/portal/")) return path;
    return pageTips[path] ? path : "/";
  }

  function tipsForPage() {
    if (path.startsWith("/portal/")) return portalTips;
    return pageTips[normalizedPath()] || pageTips["/"];
  }

  function infoForPage() {
    if (path.startsWith("/portal/")) {
      const type = path.split("/").pop().replace(".html", "").toUpperCase();
      return [`${type} transaction insight`, `Use this transaction view to connect X12 payload review with claim lifecycle evidence.`];
    }
    return pageInfo[normalizedPath()] || pageInfo["/"];
  }

  function rotate(items) {
    return items.map((_, index) => items[(index + DAILY) % items.length]);
  }

  function buildPanel() {
    const [title, summary] = infoForPage();
    const tips = rotate(tipsForPage());
    const panel = document.createElement("section");
    panel.className = "card triage-daily-panel triage-panel-enhanced";
    panel.dataset.dailyVariant = String(DAILY);
    panel.innerHTML = `
      <div class="triage-daily-visuals" aria-label="EDI and medical claim processing graphics">
        <figure><img src="/img/edi-operations-flow.svg" alt="EDI operations flow"><figcaption>EDI intake, validation, routing, and acknowledgements</figcaption></figure>
        <figure><img src="/img/x12-claim-network.svg" alt="X12 medical claim processing network"><figcaption>X12 claim, trace, validation, and payment lineage</figcaption></figure>
      </div>
      <div class="triage-daily-content">
        <span class="eyebrow">Daily operations guide</span>
        <h2>${title}</h2>
        <p>${summary}</p>
        <ol class="triage-daily-tips">
          ${tips.map((tip, index) => `<li class="${index === 0 ? "is-featured" : ""}">${tip}</li>`).join("")}
        </ol>
      </div>
    `;
    return panel;
  }

  function insertPanel() {
    const main = document.querySelector("main.container, main");
    if (!main || main.querySelector(".triage-daily-panel")) return;
    const panel = buildPanel();
    const first = main.children[0];
    if (first && first.nextSibling) main.insertBefore(panel, first.nextSibling);
    else main.appendChild(panel);
  }

  function enhancePanels() {
    const panels = document.querySelectorAll("main .card, main .cc-hero, main .cc-tier-bar, main .detail-panel, main .provider-card, main .mapper-card, main .mapping-detail");
    panels.forEach((panel, index) => {
      panel.classList.add("triage-panel-enhanced");
      panel.dataset.panelTone = String((index + DAILY) % 6);
      panel.addEventListener("focusin", () => panel.classList.add("triage-panel-active"));
      panel.addEventListener("focusout", () => {
        setTimeout(() => {
          if (!panel.contains(document.activeElement)) panel.classList.remove("triage-panel-active");
        }, 0);
      });
    });
  }

  function refreshExistingImages() {
    const candidates = Array.from(document.querySelectorAll("main img"))
      .filter((img) => {
        const src = img.getAttribute("src") || "";
        return !/triage-logo|favicon|trish|mascot|unsplash/i.test(src)
          && !img.closest(".card-photo")
          && !img.closest(".triage-daily-panel");
      });
    candidates.slice(0, 2).forEach((img, index) => {
      img.src = index % 2 === 0 ? "/img/edi-operations-flow.svg" : "/img/x12-claim-network.svg";
      img.alt = index % 2 === 0 ? "EDI operations flow" : "X12 medical claim processing network";
      img.loading = "lazy";
    });
  }

  function init() {
    document.body.dataset.dailyPanelVariant = String(DAILY);
    refreshExistingImages();
    insertPanel();
    enhancePanels();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
