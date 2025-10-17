(function () {
  const triggers = Array.from(document.querySelectorAll("[data-login-trigger]"));
  if (!triggers.length) return;

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

  triggers.forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const nextAttr = trigger.getAttribute("data-login-next") || trigger.getAttribute("href") || "/portal.html";
      const nextPath = normalizeNext(nextAttr);
      window.location.href = resolveLoginURL(nextPath);
    });
  });
})();
