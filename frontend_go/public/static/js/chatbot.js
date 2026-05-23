(function () {
  const support = window.HEDI_SUPPORT || {};
  const scout = window.HEDI_AI_SCOUT || {};
  const brand = support.brand || "HEDI Support";
  const contactEmail = support.email || null;
  const contactPhone = support.phone || null;
  const STORAGE_KEY = "hediSupportTickets";
  const CACHE_KEY = "hediSupportReplyCache";
  const TICKET_EVENT = "hedi-ticket-created";
  const promptVersion = scout.promptVersion || "turbohedi-support-v1";
  const toolOrder = Array.isArray(scout.toolOrder) ? scout.toolOrder : [];
  const SYSTEM_PLAYBOOK = [
    `prompt_version=${promptVersion}`,
    `light_context=${scout.lightContext === false ? "false" : "true"}`,
    `tool_order=${toolOrder.join(",")}`,
    "mission=guide operators through upload, search, acknowledgements, and issue intake",
    "style=short operational answers with a next step and confidence signal",
    "rules=prefer exact workflow guidance over generic AI phrasing"
  ].join("\n");

  function readTickets() {
    try {
      const existing = localStorage.getItem(STORAGE_KEY);
      if (!existing) return [];
      const parsed = JSON.parse(existing);
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      console.warn("Unable to parse support ticket log", err);
      return [];
    }
  }

  function writeTickets(tickets) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(tickets));
    } catch (err) {
      console.warn("Unable to persist support ticket log", err);
    }
  }

  function readCache() {
    try {
      const existing = localStorage.getItem(CACHE_KEY);
      if (!existing) return {};
      const parsed = JSON.parse(existing);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (err) {
      console.warn("Unable to parse support reply cache", err);
      return {};
    }
  }

  function writeCache(cache) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
    } catch (err) {
      console.warn("Unable to persist support reply cache", err);
    }
  }

  function storeTicket(ticket) {
    const tickets = readTickets();
    tickets.unshift(ticket);
    writeTickets(tickets.slice(0, (scout.contextProfile && scout.contextProfile.maxTickets) || 6));
    window.dispatchEvent(new CustomEvent(TICKET_EVENT, { detail: ticket }));
  }

  function createElement(tag, className, attrs = {}) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    Object.entries(attrs).forEach(([key, value]) => {
      if (value === null || value === undefined) return;
      if (key === "text") el.textContent = value;
      else if (key === "html") el.innerHTML = value;
      else el.setAttribute(key, value);
    });
    return el;
  }

  function formatTimestamp() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function generateRequestId() {
    const base = Date.now().toString(36).toUpperCase();
    const suffix = Math.random().toString(36).substring(2, 6).toUpperCase();
    return `TRISH-${base}${suffix}`;
  }

  function makeMailLink(id, summary) {
    if (!contactEmail) return "";
    const subject = encodeURIComponent(`${brand} trouble ticket ${id}`);
    const body = encodeURIComponent(`Request ${id}\n\nSummary:\n${summary || ""}`);
    return `<a href="mailto:${contactEmail}?subject=${subject}&body=${body}">Email ${brand}</a>`;
  }

  function makeSmsLink(id, summary) {
    if (!contactPhone) return "";
    const clean = contactPhone.replace(/[^+0-9]/g, "");
    const body = encodeURIComponent(`Request ${id}: ${summary || ""}`);
    return `<a href="sms:${clean}?&body=${body}">Text ${brand}</a>`;
  }

  function parseSeverity(text) {
    const match = text.match(/\b(?:sev(?:erity)?\s*)?([1-4])\b/);
    return match ? parseInt(match[1], 10) : null;
  }

  function formatSeverity(severity) {
    return {
      1: "1 — Critical outage",
      2: "2 — High impact",
      3: "3 — Degraded",
      4: "4 — Question / heads-up",
    }[severity] || "Unspecified";
  }

  function currentPageProfile() {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search || "");
    return {
      page: path,
      uploadedBy: params.get("uploaded_by") || params.get("uploadedBy") || "",
      tradingPartner: params.get("trading_partner_id") || params.get("partner") || "",
      jobId: params.get("job_id") || "",
      status: document.querySelector(".status-panel[data-tone]")?.dataset.tone || "",
      validation: document.querySelector(".status.status-invalid, .status.status-valid, .status.status-error")?.textContent || ""
    };
  }

  function cacheKey(message, context) {
    return JSON.stringify({ v: promptVersion, p: context.page, m: message.trim().toLowerCase() });
  }

  function getCachedReply(message, context) {
    const cache = readCache();
    return cache[cacheKey(message, context)] || null;
  }

  function setCachedReply(message, context, responses) {
    const cache = readCache();
    cache[cacheKey(message, context)] = responses;
    writeCache(cache);
  }

  function buildConfidenceLine(label, detail) {
    return `<strong>${label}:</strong> ${detail}`;
  }

  let pendingTicket = null;

  function buildResponses(message, context) {
    const text = message.trim();
    const lower = text.toLowerCase();
    const responses = [];
    const severityFromText = parseSeverity(lower);

    if (pendingTicket) {
      if (!severityFromText) {
        return ["I still need a severity from 1 (critical) to 4 (question) so I know how loudly to page the team."];
      }
      pendingTicket.severity = severityFromText;
      pendingTicket.status = pendingTicket.status || "open";
      storeTicket(pendingTicket);
      const emailLink = makeMailLink(pendingTicket.id, pendingTicket.summary);
      const smsLink = makeSmsLink(pendingTicket.id, pendingTicket.summary);
      const contactActions = [emailLink, smsLink].filter(Boolean).join(" · ");
      const submittedAt = new Date(pendingTicket.createdAt).toLocaleString();
      responses.push(
        `Logged ${pendingTicket.id} at ${submittedAt} with severity ${formatSeverity(pendingTicket.severity)}. ${contactActions || "Reach out to support with this ID and severity."}`
      );
      pendingTicket = null;
    }

    if (/(upload|submit|queue|837)/.test(lower)) {
      responses.push(
        [
          "Queue flow:",
          "1) Enter Login ID and Trading Partner.",
          "2) Upload the X12 file and wait for the job ID banner.",
          "3) Open Import Detail to inspect validation, claims, acknowledgements, and audit trail.",
          buildConfidenceLine("Confidence", "TurboHEDI persists the raw payload before it publishes the worker job.")
        ].join("\n")
      );
    }
    if (/(processed|status|outputs|ack|999|277)/.test(lower)) {
      responses.push(
        [
          "Review flow:",
          "Search by Login ID or Trading Partner, then open the job detail.",
          "Use Overview for the summary, Validation Issues for failures, and Acknowledgements for 999 / 277CA downloads.",
          buildConfidenceLine("Confidence", "The detail page shows audit events and acknowledgement counts from the live import record.")
        ].join("\n")
      );
    }
    if (/(mapping|segment|grid|cms|projection)/.test(lower)) {
      responses.push(
        [
          "Mapping flow:",
          "Use Parsed JSON and CMS Projection together to compare extracted claim content against normalized downstream fields.",
          "If a claim fails validation, start with the issue code and the raw CLM / SV1 segments before editing maps.",
          buildConfidenceLine("Confidence", "The support playbook stays frozen under " + promptVersion + " so operator guidance does not drift.")
        ].join("\n")
      );
    }
    if (/(error|trouble|help|fail|issue|down)/.test(lower)) {
      const requestId = generateRequestId();
      const createdAt = new Date().toISOString();
      const ticket = {
        id: requestId,
        summary: text,
        createdAt,
        severity: severityFromText || null,
        status: "open",
        context,
      };
      if (ticket.severity) {
        storeTicket(ticket);
        const emailLink = makeMailLink(ticket.id, ticket.summary);
        const smsLink = makeSmsLink(ticket.id, ticket.summary);
        const contactActions = [emailLink, smsLink].filter(Boolean).join(" · ");
        responses.push(
          `Logged ${ticket.id} with severity ${formatSeverity(ticket.severity)}. ${contactActions || "Reach out to support with this ID and severity."}`
        );
      } else {
        pendingTicket = ticket;
        responses.push(`I created ticket ${ticket.id}. How severe is it on a scale of 1 (totally down) to 4 (question)?`);
      }
    }

    if (!responses.length) {
      responses.push(
        `I can guide uploads, import review, acknowledgements, CMS projection checks, or ticket intake. Current page: ${context.page || "/"}.`
      );
    }

    return responses;
  }

  function respondForMessage(message, channel) {
    const text = message.trim();
    if (!text) return;
    const context = currentPageProfile();
    const cached = getCachedReply(text, context);
    const responses = cached || buildResponses(text, context);
    if (!cached) setCachedReply(text, context, responses);
    responses.forEach((answer) => channel(answer, "bot"));
  }

  function initChatbot() {
    if (document.querySelector(".trish-chat")) return;

    const wrapper = createElement("div", "trish-chat");
    const toggle = createElement("button", "trish-chat__toggle", {
      type: "button",
      "aria-expanded": "false",
      title: "Operations Support",
    });
    toggle.innerHTML = `<span>Support</span>`;

    const windowEl = createElement("div", "trish-chat__window", {
      role: "dialog",
      "aria-live": "polite",
      "aria-label": "Customer support chat",
    });
    const header = createElement("div", "trish-chat__header");
    header.innerHTML = `<div class="trish-chat__identity"><div><strong>Support</strong><small>${brand}</small><small>${promptVersion}</small></div></div>`;
    const closeBtn = createElement("button", "trish-chat__close", { type: "button", title: "Close chat" });
    closeBtn.innerHTML = "&times;";
    header.appendChild(closeBtn);

    const body = createElement("div", "trish-chat__body");
    const messages = createElement("div", "trish-chat__messages");
    body.appendChild(messages);

    const form = createElement("form", "trish-chat__composer");
    const input = createElement("input", "trish-chat__input", {
      type: "text",
      placeholder: "Ask about uploads, acknowledgements, or job review"
    });
    const sendBtn = createElement("button", "trish-chat__send", { type: "submit" });
    sendBtn.textContent = "Send";
    form.append(input, sendBtn);

    const quickActions = createElement("div", "trish-chat__quick-actions");
    [
      { label: "Upload playbook", value: "How do I upload files?" },
      { label: "Ack review", value: "Where do I find 999 or 277CA outputs?" },
      { label: "Log an issue", value: "I hit an error and need help" },
    ].forEach(({ label, value }) => {
      const btn = createElement("button", "trish-chat__quick", { type: "button" });
      btn.textContent = label;
      btn.addEventListener("click", () => {
        addMessage(value, "user");
        respondForMessage(value, addMessage);
      });
      quickActions.appendChild(btn);
    });

    windowEl.append(header, body, quickActions, form);
    wrapper.append(toggle, windowEl);
    document.body.appendChild(wrapper);

    function addMessage(text, role) {
      const bubble = createElement("div", `trish-chat__message trish-chat__message--${role}`);
      const timestamp = createElement("span", "trish-chat__time", { text: formatTimestamp() });
      const content = createElement("div", "trish-chat__bubble", { html: String(text).replace(/\n/g, "<br>") });
      bubble.append(content, timestamp);
      messages.appendChild(bubble);
      messages.scrollTop = messages.scrollHeight;
    }

    function openChat() {
      wrapper.classList.add("trish-chat--open");
      toggle.setAttribute("aria-expanded", "true");
      input.focus();
    }

    function closeChat() {
      wrapper.classList.remove("trish-chat--open");
      toggle.setAttribute("aria-expanded", "false");
    }

    toggle.addEventListener("click", () => {
      if (wrapper.classList.contains("trish-chat--open")) closeChat();
      else openChat();
    });
    closeBtn.addEventListener("click", closeChat);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return input.focus();
      addMessage(text, "user");
      respondForMessage(text, addMessage);
      input.value = "";
      input.focus();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && wrapper.classList.contains("trish-chat--open")) closeChat();
    });

    addMessage(
      `Support desk ready. ${brand} is using the frozen ${promptVersion} playbook with light context and cached replies for repeat questions.`,
      "bot"
    );
    console.debug("TurboHEDI support playbook", { SYSTEM_PLAYBOOK, scoutRouting: scout.routing || null });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChatbot);
  } else {
    initChatbot();
  }
})();
