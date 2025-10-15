(function () {
  const support = window.HEDI_SUPPORT || {};
  const brand = support.brand || "HEDI Support";
  const contactEmail = support.email || null;
  const contactPhone = support.phone || null;

  function createElement(tag, className, attrs = {}) {
    const el = document.createElement(tag);
    if (className) {
      el.className = className;
    }
    Object.entries(attrs).forEach(([key, value]) => {
      if (value === null || value === undefined) return;
      if (key === "text") {
        el.textContent = value;
      } else if (key === "html") {
        el.innerHTML = value;
      } else {
        el.setAttribute(key, value);
      }
    });
    return el;
  }

  function formatTimestamp() {
    const date = new Date();
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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

  function respondForMessage(message, channel) {
    const text = message.trim();
    if (!text) {
      return;
    }

    const lower = text.toLowerCase();
    const responses = [];

    if (/(upload|submit|queue)/.test(lower)) {
      responses.push("To upload claims, pick your X12 file, set Login ID and Trading Partner, then tap Queue Files. HEDI stores the job ID instantly and you can monitor it in Find Your Submissions.");
    }
    if (/(processed|status|outputs)/.test(lower)) {
      responses.push("Processed Files shows every acknowledgement we captured. Search by Login ID, open Outputs, and download a 999 or 277CA instantly.");
    }
    if (/(mapping|segment|grid)/.test(lower)) {
      responses.push("Drag identifiers like ISA, GS, and ST into the canvas. Each row mirrors a positional XML view so you can see where the segments land.");
    }
    if (/(error|trouble|help|fail|issue)/.test(lower)) {
      const requestId = generateRequestId();
      const emailLink = makeMailLink(requestId, text);
      const smsLink = makeSmsLink(requestId, text);
      const contactActions = [emailLink, smsLink].filter(Boolean).join(" · ");
      const followUp = contactActions || "Reach out to support with this ID.";
      responses.push(`I logged reference ${requestId}. ${followUp}`);
    }
    if (!responses.length) {
      responses.push("I can help with uploads, mapping, processed outputs, and raising trouble tickets. Ask about any of those or say \"help\" for quick tips.");
    }

    responses.forEach((answer) => channel(answer, "bot"));
  }

  function initChatbot() {
    if (document.querySelector(".trish-chat")) {
      return;
    }

    const wrapper = createElement("div", "trish-chat");
    const toggle = createElement("button", "trish-chat__toggle", { type: "button", "aria-expanded": "false", title: "Chat with Trish" });
    toggle.innerHTML = `<img src="/img/trish-laptop.svg" alt="Trish avatar" loading="lazy"><span>Need help?</span>`;

    const windowEl = createElement("div", "trish-chat__window", { role: "dialog", "aria-live": "polite", "aria-label": "Customer support chat" });
    const header = createElement("div", "trish-chat__header");
    header.innerHTML = `<div class="trish-chat__identity"><img src="/img/trish-laptop.svg" alt="Trish"><div><strong>Trish</strong><small>${brand}</small></div></div>`;
    const closeBtn = createElement("button", "trish-chat__close", { type: "button", title: "Close chat" });
    closeBtn.innerHTML = "&times;";
    header.appendChild(closeBtn);

    const body = createElement("div", "trish-chat__body");
    const messages = createElement("div", "trish-chat__messages");
    body.appendChild(messages);

    const form = createElement("form", "trish-chat__composer");
    const input = createElement("input", "trish-chat__input", { type: "text", placeholder: "Ask Trish how to…" });
    const sendBtn = createElement("button", "trish-chat__send", { type: "submit" });
    sendBtn.textContent = "Send";
    form.append(input, sendBtn);

    const quickActions = createElement("div", "trish-chat__quick-actions");
    [
      { label: "How do I upload?", value: "How do I upload files?" },
      { label: "Where are outputs?", value: "Where do I find processed outputs?" },
      { label: "Log a trouble ticket", value: "I hit an error and need help" }
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
      const content = createElement("div", "trish-chat__bubble");
      content.innerHTML = text.replace(/\n/g, "<br>");
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
      if (wrapper.classList.contains("trish-chat--open")) {
        closeChat();
      } else {
        openChat();
      }
    });

    closeBtn.addEventListener("click", closeChat);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) {
        input.focus();
        return;
      }
      addMessage(text, "user");
      respondForMessage(text, addMessage);
      input.value = "";
      input.focus();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && wrapper.classList.contains("trish-chat--open")) {
        closeChat();
      }
    });

    addMessage("Hi there! I'm Trish. Ask me how to upload, map segments, review processed files, or say 'error' if something went sideways.", "bot");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChatbot);
  } else {
    initChatbot();
  }
})();
