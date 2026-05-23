(function () {
  const ADMIN_ROLE = "administrator";
  const ROLE_ORDER = ["view", "submit", ADMIN_ROLE];
  const ROLE_ALIASES = {
    admin: ADMIN_ROLE,
    administrator: ADMIN_ROLE,
    submitter: "submit",
    submit: "submit",
    create: "submit",
    update: "submit",
    editor: "submit",
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
    if (typeof window.TRIAGE_PATH_PREFIX === "string") {
      const configured = normalizePrefix(window.TRIAGE_PATH_PREFIX);
      if (configured) {
        return configured;
      }
    }
    return "";
  })();

  window.TRIAGE_PATH_PREFIX = PATH_PREFIX;

  if (typeof window.triageResolve !== "function") {
    window.triageResolve = (path) => {
      if (!path || path[0] !== "/") {
        return path;
      }
      if (!PATH_PREFIX) {
        return path;
      }
      return `${PATH_PREFIX}${path}`;
    };
  }

  const OAUTH_START_BASE = (() => {
    if (typeof window.TRIAGE_OAUTH2_START === "string") {
      const trimmed = window.TRIAGE_OAUTH2_START.trim();
      if (trimmed) return trimmed;
    }
    return "/oauth2/start";
  })();

  const DEFAULT_PROFILE = {
    username: null,
    role: "view",
    allowPortal: false,
    allowAdmin: false,
    allowSubmit: false,
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

  function buildLoginURL(nextPath) {
    const target = sanitizeNextPath(nextPath);
    const separator = OAUTH_START_BASE.includes("?") ? "&" : "?";
    return `${OAUTH_START_BASE}${separator}rd=${encodeURIComponent(target || "/")}`;
  }

  window.triageLoginURL = buildLoginURL;

  function redirectToLogin() {
    if (!requiresAuth || redirecting) return;
    const currentPath = sanitizeNextPath(window.location.pathname + window.location.search);
    if (currentPath === "/" || currentPath.startsWith("/oauth2/")) {
      return;
    }
    redirecting = true;
    window.location.href = buildLoginURL(currentPath || "/");
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

  function resolveEffectiveRole(role, allowAdminFlag) {
    const normalizedRole = normalizeRole(role);
    if (allowAdminFlag || normalizedRole === ADMIN_ROLE) {
      return ADMIN_ROLE;
    }
    return normalizedRole;
  }

  function applyAuthz() {
    const effectiveRole = resolveEffectiveRole(profile.role, profile.allowAdmin);
    const submitIndex = ROLE_INDEX["submit"] ?? ROLE_INDEX[ADMIN_ROLE] ?? 0;
    const effectiveIndex = ROLE_INDEX[effectiveRole] ?? 0;
    const allowSubmit = (profile.allowSubmit || effectiveIndex >= submitIndex) ? true : false;
    const allowAdmin = effectiveRole === ADMIN_ROLE;
    const allowPortal = allowAdmin || allowSubmit || effectiveRole === ROLE_ORDER[0];
    profile.role = effectiveRole;
    profile.allowAdmin = allowAdmin;
    profile.allowPortal = allowPortal;
    profile.allowSubmit = allowSubmit;
    const index = effectiveIndex;
    document.body.dataset.userRole = effectiveRole;
    if (profile.username) {
      document.body.dataset.userName = profile.username;
    } else {
      delete document.body.dataset.userName;
    }
    document.body.dataset.allowPortal = allowPortal ? "true" : "false";
    document.body.dataset.allowAdmin = allowAdmin ? "true" : "false";
    document.body.dataset.allowSubmit = allowSubmit ? "true" : "false";
    document.body.dataset.defaultDestination = profile.defaultDestination || "/";

    document
      .querySelectorAll("[data-requires-role]")
      .forEach((el) => enforceElement(el, index));

    window.dispatchEvent(
      new CustomEvent("triage-role-changed", {
        detail: {
          user: profile.username,
          role: profile.role,
          allowPortal: profile.allowPortal,
          allowAdmin: profile.allowAdmin,
          allowSubmit: profile.allowSubmit,
        },
      }),
    );
  }

  async function fetchProfile() {
    try {
      const url = window.triageResolve ? window.triageResolve("/auth/me") : "/auth/me";
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
      const effectiveRole = resolveEffectiveRole(normalizedRole, allowAdmin);
      const derivedAllowSubmit = (ROLE_INDEX[effectiveRole] ?? 0) >= (ROLE_INDEX["submit"] ?? 1);
      const allowSubmit =
        typeof data.allow_submit === "boolean"
          ? data.allow_submit || derivedAllowSubmit
          : derivedAllowSubmit;
      profile = {
        username: data.username || null,
        role: effectiveRole,
        allowPortal:
          data.allow_portal !== undefined
            ? Boolean(data.allow_portal)
            : allowAdmin || allowSubmit || effectiveRole === ROLE_ORDER[0],
        allowAdmin,
        allowSubmit,
        defaultDestination: data.default_destination || "/",
      };
      window.TRIAGE_AUTHZ = window.TRIAGE_AUTHZ || {};
      window.TRIAGE_AUTHZ.currentUser = profile.username;
      window.TRIAGE_AUTHZ.currentRole = profile.role;
      window.TRIAGE_AUTHZ.defaultRole = window.TRIAGE_AUTHZ.defaultRole || "view";
      window.TRIAGE_AUTHZ.defaultDestination = profile.defaultDestination;
    } catch (err) {
      console.warn("Unable to load profile", err);
      profile = { ...DEFAULT_PROFILE };
      if (requiresAuth) {
        redirectToLogin();
      }
    }
    applyAuthz();
  }

  window.refreshTriageRole = fetchProfile;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fetchProfile);
  } else {
    fetchProfile();
  }

  window.addEventListener("triage-role-refresh", fetchProfile);
})();
