(function () {
  function normalizeNext(value) {
    if (typeof value !== "string" || !value.trim()) return "/portal.html";
    const trimmed = value.trim();
    if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
      return "/portal.html";
    }
    if (!trimmed.startsWith("/")) {
      return `/${trimmed.replace(/^\/+/, "")}`;
    }
    return trimmed;
  }

  function resolveLoginURL(nextPath) {
    if (typeof window.hediLoginURL === "function") {
      return window.hediLoginURL(nextPath);
    }
    const base = (typeof window.HEDI_OAUTH2_START === "string" && window.HEDI_OAUTH2_START.trim()) || "/oauth2/start";
    const separator = base.includes("?") ? "&" : "?";
    return `${base}${separator}rd=${encodeURIComponent(nextPath || "/")}`;
  }

  function handleLoginTrigger(event) {
    const trigger = event.target.closest("[data-login-trigger]");
    if (!trigger) return;
    if (trigger.hasAttribute("data-login-trigger-disabled")) {
      return;
    }
    event.preventDefault();
    const nextAttr = trigger.getAttribute("data-login-next") || trigger.getAttribute("href") || "/portal.html";
    const nextPath = normalizeNext(nextAttr);
    window.location.href = resolveLoginURL(nextPath);
  }

  function applyNavGuards() {
    if (!document.body) return;
    const allowPortal = document.body.dataset.allowPortal === "true";
    const loggedIn = Boolean(document.body.dataset.userName);
    const shouldPrompt = !(loggedIn && allowPortal);
    const links = Array.from(document.querySelectorAll(".portal-only .portal-link"));
    links.forEach((link) => {
      if (shouldPrompt) {
        if (!link.dataset.loginTrigger) {
          link.dataset.loginTrigger = "nav";
        }
        if (!link.dataset.loginNext) {
          link.dataset.loginNext = link.getAttribute("href") || "/portal.html";
          link.dataset.loginNextSource = "nav";
        }
      } else if (link.dataset.loginTrigger === "nav") {
        delete link.dataset.loginTrigger;
        if (link.dataset.loginNextSource === "nav") {
          delete link.dataset.loginNext;
          delete link.dataset.loginNextSource;
        }
      }
    });
  }

  document.addEventListener("click", handleLoginTrigger, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyNavGuards);
  } else {
    applyNavGuards();
  }

  window.addEventListener("hedi-role-changed", applyNavGuards);
})();
