(function () {
  const ADMIN_ROLE = "administrator";
  const ROLE_ORDER = ["view", "create", "update", ADMIN_ROLE];
  const ROLE_ALIASES = {
    admin: ADMIN_ROLE,
    administrator: ADMIN_ROLE,
  };
  function normalizeRole(role) {
    if (!role) return ROLE_ORDER[0];
    const cleaned = String(role).trim().toLowerCase();
    return ROLE_ALIASES[cleaned] || (ROLE_ORDER.includes(cleaned) ? cleaned : ROLE_ORDER[0]);
  }
  const ROLE_INDEX = ROLE_ORDER.reduce((acc, role, idx) => {
    acc[role] = idx;
    return acc;
  }, {});
  const guardHandlers = new WeakMap();

  function normalizePrefix(prefix) {
    if (!prefix) return "";
    if (prefix === "/") return "";
    return prefix.replace(/\/+$/, "");
  }

  const PATH_PREFIX = (() => {
    const fallback =
      typeof window.HEDI_PATH_PREFIX === "string"
        ? normalizePrefix(window.HEDI_PATH_PREFIX)
        : "";
    const path = window.location.pathname || "";
    if (!path) {
      return fallback;
    }
    const idx = path.lastIndexOf("/");
    if (idx <= 0) {
      return fallback;
    }
    const derived = normalizePrefix(path.slice(0, idx));
    if (derived) {
      return derived;
    }
    return fallback;
  })();

  window.HEDI_PATH_PREFIX = PATH_PREFIX;

  if (typeof window.hediResolve !== "function") {
    window.hediResolve = (path) => {
      if (!path || path[0] !== "/") {
        return path;
      }
      if (!PATH_PREFIX) {
        return path;
      }
      return `${PATH_PREFIX}${path}`;
    };
  }

  const DEFAULT_PROFILE = {
    username: null,
    role: "view",
    allowPortal: false,
    allowAdmin: false,
    defaultDestination: "/",
  };

  let profile = { ...DEFAULT_PROFILE };
  let redirecting = false;
  const requiresAuth = (document.body && document.body.getAttribute("data-requires-auth")) || "";

  function sanitizeNextPath(path) {
    if (!path) return "/";
    if (path.startsWith("http://") || path.startsWith("https://")) return "/";
    if (!path.startsWith("/")) return "/";
    return path;
  }

  function redirectToLogin() {
    if (!requiresAuth || redirecting) return;
    const currentPath = sanitizeNextPath(window.location.pathname + window.location.search);
    if (currentPath === "/" || currentPath.startsWith("/login")) {
      return;
    }
    redirecting = true;
    const nextParam = encodeURIComponent(currentPath || "/");
    window.location.href = `/login?next=${nextParam}`;
  }

  function enforceElement(el, roleIndex) {
    const requiredRaw = el.getAttribute("data-requires-role");
    const required = normalizeRole(requiredRaw);
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
      const label = required === ADMIN_ROLE ? "administrator" : required;
      el.setAttribute("title", `Requires ${label} access`);

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
    const normalizedRole = normalizeRole(profile.role);
    const allowAdmin = normalizedRole === ADMIN_ROLE || Boolean(profile.allowAdmin);
    const allowPortal = Boolean(profile.allowPortal || allowAdmin);
    profile.role = normalizedRole;
    profile.allowAdmin = allowAdmin;
    profile.allowPortal = allowPortal;
    const index = ROLE_INDEX[normalizedRole] ?? 0;
    document.body.dataset.userRole = normalizedRole;
    if (profile.username) {
      document.body.dataset.userName = profile.username;
    } else {
      delete document.body.dataset.userName;
    }
    document.body.dataset.allowPortal = allowPortal ? "true" : "false";
    document.body.dataset.allowAdmin = allowAdmin ? "true" : "false";
    document.body.dataset.defaultDestination = profile.defaultDestination || "/";

    document
      .querySelectorAll("[data-requires-role]")
      .forEach((el) => enforceElement(el, index));

    window.dispatchEvent(
      new CustomEvent("hedi-role-changed", {
        detail: {
          user: profile.username,
          role: profile.role,
          allowPortal: profile.allowPortal,
          allowAdmin: profile.allowAdmin,
        },
      }),
    );
  }

  async function fetchProfile() {
    try {
      const url = window.hediResolve ? window.hediResolve("/auth/me") : "/auth/me";
      const response = await fetch(url, { credentials: "same-origin" });
      if (!response.ok) {
        profile = { ...DEFAULT_PROFILE };
        applyAuthz();
        if (response.status === 401 || response.status === 403) {
          redirectToLogin();
        }
        return;
      }
      const data = await response.json();
      const normalizedRole = normalizeRole(data.role || "view");
      const allowAdmin = normalizedRole === ADMIN_ROLE || Boolean(data.allow_admin);
      profile = {
        username: data.username || null,
        role: normalizedRole,
        allowPortal: Boolean(data.allow_portal !== undefined ? data.allow_portal : true),
        allowAdmin,
        defaultDestination: data.default_destination || "/",
      };
      window.HEDI_AUTHZ = window.HEDI_AUTHZ || {};
      window.HEDI_AUTHZ.currentUser = profile.username;
      window.HEDI_AUTHZ.currentRole = profile.role;
      window.HEDI_AUTHZ.defaultRole = window.HEDI_AUTHZ.defaultRole || "view";
      window.HEDI_AUTHZ.defaultDestination = profile.defaultDestination;
    } catch (err) {
      console.warn("Unable to load profile", err);
      profile = { ...DEFAULT_PROFILE };
      if (requiresAuth) {
        redirectToLogin();
      }
    }
    applyAuthz();
  }

  window.refreshHediRole = fetchProfile;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fetchProfile);
  } else {
    fetchProfile();
  }

  window.addEventListener("hedi-role-refresh", fetchProfile);
})();
