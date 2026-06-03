// Triage CMS validation panel.
//
// Self-contained, opt-in module. Find any element with `data-turbo-validate`
// and wire its child input/buttons to POST against /turbo/validate or
// /turbo/pipeline. Renders the resulting ValidationReport + ScrubReport
// inline. Does not touch or depend on mapper.js.
(function () {
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderHeader(report) {
    const cls = report.valid ? "tv-ok" : "tv-fail";
    const wrap = el("div", "tv-header " + cls);
    wrap.appendChild(el("strong", "tv-txn",
      (report.transaction_set || "?") + " "
      + (report.implementation_version || "")));
    wrap.appendChild(el("span", "tv-status",
      report.valid ? "VALID" : "INVALID"));
    wrap.appendChild(el("span", "tv-count",
      report.error_count + " error(s)"));
    wrap.appendChild(el("span", "tv-count",
      report.warning_count + " warning(s)"));
    wrap.appendChild(el("span", "tv-count",
      report.claim_count + " claim(s)"));
    return wrap;
  }

  function renderSnipSummary(summary) {
    if (!summary) return null;
    const list = el("ul", "tv-snip");
    Object.keys(summary).forEach(function (level) {
      const counts = summary[level];
      const li = el("li", "tv-snip-row");
      li.appendChild(el("span", "tv-snip-level",
        level.replace(/^snip\d+_/, "")));
      li.appendChild(el("span", "tv-snip-counts",
        counts.errors + "e / " + counts.warnings + "w"));
      list.appendChild(li);
    });
    return list;
  }

  function renderIssues(issues, limit) {
    if (!issues || !issues.length) return null;
    const list = el("ul", "tv-issues");
    issues.slice(0, limit).forEach(function (issue) {
      const li = el("li", "tv-issue tv-issue--" + (issue.severity || "info"));
      li.appendChild(el("span", "tv-issue-sev",
        String(issue.severity || "").toUpperCase()));
      li.appendChild(el("span", "tv-issue-code", issue.code));
      const where = issue.element_ref || issue.segment_id
        || (issue.loop_id ? "Loop " + issue.loop_id : "—");
      li.appendChild(el("span", "tv-issue-where", where));
      li.appendChild(el("span", "tv-issue-msg", issue.message));
      list.appendChild(li);
    });
    if (issues.length > limit) {
      list.appendChild(el("li", "tv-issue-more",
        "... " + (issues.length - limit) + " more"));
    }
    return list;
  }

  function renderScrub(scrub) {
    if (!scrub) return null;
    const wrap = el("div", "tv-scrub");
    const header = el("div", "tv-scrub-header " + (scrub.clean ? "tv-ok" : "tv-fail"));
    header.appendChild(el("strong", null, "CMS scrubbing"));
    header.appendChild(el("span", "tv-status", scrub.clean ? "CLEAN" : "FLAGGED"));
    header.appendChild(el("span", "tv-count", scrub.finding_count + " finding(s)"));
    header.appendChild(el("span", "tv-count", scrub.deny_count + " deny"));
    header.appendChild(el("span", "tv-count", scrub.review_count + " review"));
    wrap.appendChild(header);
    if (scrub.category_summary) {
      const cats = el("ul", "tv-cats");
      Object.keys(scrub.category_summary).forEach(function (key) {
        const li = el("li", "tv-cat");
        li.appendChild(el("span", "tv-cat-key", key));
        li.appendChild(el("span", "tv-cat-count",
          String(scrub.category_summary[key])));
        cats.appendChild(li);
      });
      wrap.appendChild(cats);
    }
    if (scrub.findings && scrub.findings.length) {
      const list = el("ul", "tv-findings");
      scrub.findings.slice(0, 25).forEach(function (f) {
        const li = el("li", "tv-finding tv-finding--" + (f.severity || "advisory"));
        li.appendChild(el("span", "tv-finding-sev",
          String(f.severity || "").toUpperCase()));
        li.appendChild(el("span", "tv-finding-cat", f.category));
        li.appendChild(el("span", "tv-finding-code", f.code));
        li.appendChild(el("span", "tv-finding-msg", f.message));
        list.appendChild(li);
      });
      wrap.appendChild(list);
    }
    return wrap;
  }

  function renderResult(payload, includeScrub) {
    const wrap = el("div", "tv-result");
    const validation = includeScrub ? payload.validation : payload;
    wrap.appendChild(renderHeader(validation));
    const snip = renderSnipSummary(validation.snip_summary);
    if (snip) wrap.appendChild(snip);
    const issues = renderIssues(validation.issues, 25);
    if (issues) wrap.appendChild(issues);
    if (includeScrub) {
      const scrub = renderScrub(payload.scrubbing);
      if (scrub) wrap.appendChild(scrub);
    }
    return wrap;
  }

  async function call(panel, mode) {
    const input = panel.querySelector(".turbo-validate-input");
    const out = panel.querySelector("[data-turbo-validate-results]");
    if (!input || !out) return;
    const x12 = (input.value || "").trim();
    out.innerHTML = "";
    out.appendChild(el("p", "tv-status-msg", "Running…"));
    if (!x12) {
      out.innerHTML = "";
      out.appendChild(el("p", "tv-status-msg",
        "Paste an X12 transaction (starting with ISA) and click again."));
      return;
    }
    const url = mode === "pipeline" ? "/turbo/pipeline" : "/turbo/validate";
    const body = mode === "pipeline"
      ? { x12: x12, scrub: true, to_fhir: false, generate_acks: false }
      : { x12: x12 };
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "same-origin",
      });
      if (!response.ok) {
        const text = await response.text();
        out.innerHTML = "";
        out.appendChild(el("p", "tv-status-msg tv-fail",
          "Error " + response.status + ": " + text));
        return;
      }
      const data = await response.json();
      out.innerHTML = "";
      out.appendChild(renderResult(data, mode === "pipeline"));
    } catch (err) {
      out.innerHTML = "";
      out.appendChild(el("p", "tv-status-msg tv-fail",
        "Network error: " + (err && err.message ? err.message : err)));
    }
  }

  function init() {
    const panels = document.querySelectorAll("[data-turbo-validate]");
    panels.forEach(function (panel) {
      const validateBtn = panel.querySelector("[data-turbo-validate-run]");
      const pipelineBtn = panel.querySelector("[data-turbo-validate-pipeline]");
      if (validateBtn) {
        validateBtn.addEventListener("click", function () { call(panel, "validate"); });
      }
      if (pipelineBtn) {
        pipelineBtn.addEventListener("click", function () { call(panel, "pipeline"); });
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  if (typeof window !== "undefined") {
    window.TurboValidate = { call: call, render: renderResult };
  }
})();
