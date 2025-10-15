(function () {
  const DIRECTORY_KEY = "hediUserDirectory";
  const ACTIVE_KEY = "hediActiveUser";
  const ROLE_ORDER = ["view", "update", "create", "admin"];
  const ROLE_INDEX = ROLE_ORDER.reduce((acc, role, idx) => {
    acc[role] = idx;
    return acc;
  }, {});
  const guardHandlers = new WeakMap();

  function loadDirectory() {
    try {
      const raw = localStorage.getItem(DIRECTORY_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (err) {
      console.warn("Unable to parse user directory", err);
      return {};
    }
  }

  function resolveActiveUser() {
    const config = window.HEDI_AUTHZ || {};
    const explicit = config.currentUser || null;
    const stored = localStorage.getItem(ACTIVE_KEY) || sessionStorage.getItem(ACTIVE_KEY);
    return explicit || stored || null;
  }

  function computeRole() {
    const directory = loadDirectory();
    const activeUser = resolveActiveUser();
    const config = window.HEDI_AUTHZ || {};
    const fallback = config.defaultRole || "view";
    const entry = activeUser ? directory[activeUser] : null;
    const role = entry && entry.role ? entry.role : fallback;
    return { directory, activeUser, role };
  }

  function enforceElement(el, roleIndex) {
    const required = el.getAttribute("data-requires-role");
    if (!required) return;
    const needIndex = ROLE_INDEX[required] ?? 0;
    if (roleIndex >= needIndex) {
      el.classList.remove("is-disabled");
      el.removeAttribute("aria-disabled");
      if (guardHandlers.has(el)) {
        el.removeEventListener("click", guardHandlers.get(el));
        guardHandlers.delete(el);
      }
      if (el instanceof HTMLButtonElement || el instanceof HTMLInputElement || el instanceof HTMLSelectElement) {
        el.disabled = false;
      }
      if (el.getAttribute("tabindex") === "-1" && !el.dataset.tabindexLocked) {
        el.removeAttribute("tabindex");
      }
      if (Object.prototype.hasOwnProperty.call(el.dataset, "lockedTitle")) {
        if (el.dataset.lockedTitle) {
          el.setAttribute("title", el.dataset.lockedTitle);
        } else {
          el.removeAttribute("title");
        }
        delete el.dataset.lockedTitle;
      }
      return;
    }

    el.classList.add("is-disabled");
    el.setAttribute("aria-disabled", "true");
    if (!el.dataset.lockedTitle) {
      el.dataset.lockedTitle = el.getAttribute("title") || "";
    }
    el.setAttribute("title", `Requires ${required} access`);

    if (el instanceof HTMLButtonElement || el instanceof HTMLInputElement || el instanceof HTMLSelectElement) {
      el.disabled = true;
    } else {
      el.setAttribute("tabindex", "-1");
      if (!guardHandlers.has(el)) {
        const handler = (event) => {
          event.preventDefault();
          event.stopPropagation();
        };
        guardHandlers.set(el, handler);
        el.addEventListener("click", handler);
      }
    }
  }

  function applyAuthz() {
    const { activeUser, role } = computeRole();
    const index = ROLE_INDEX[role] ?? 0;
    document.body.dataset.userRole = role;
    if (activeUser) {
      document.body.dataset.userName = activeUser;
    } else {
      delete document.body.dataset.userName;
    }
    document.querySelectorAll("[data-requires-role]").forEach((el) => enforceElement(el, index));
    window.dispatchEvent(new CustomEvent("hedi-role-changed", { detail: { user: activeUser, role } }));
  }

  window.refreshHediRole = applyAuthz;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyAuthz);
  } else {
    applyAuthz();
  }

  window.addEventListener("storage", (event) => {
    if (event.key === DIRECTORY_KEY || event.key === ACTIVE_KEY) {
      applyAuthz();
    }
  });

  window.addEventListener("hedi-users-updated", applyAuthz);
})();
