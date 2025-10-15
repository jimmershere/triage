(function () {
  const TICKET_KEY = "hediSupportTickets";
  const USER_KEY = "hediUserDirectory";
  const ACTIVE_KEY = "hediActiveUser";
  const PROVIDER_KEY = "hediAuthProviders";
  const ROLE_ORDER = ["view", "update", "create", "admin"];

  function loadFromStorage(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return fallback;
      const parsed = JSON.parse(raw);
      return parsed ?? fallback;
    } catch (err) {
      console.warn("Failed to load", key, err);
      return fallback;
    }
  }

  function saveToStorage(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      console.warn("Failed to persist", key, err);
    }
  }

  function loadTickets() {
    return loadFromStorage(TICKET_KEY, []);
  }

  function saveTickets(tickets) {
    saveToStorage(TICKET_KEY, tickets);
  }

  function loadUsers() {
    const data = loadFromStorage(USER_KEY, {});
    return data && typeof data === "object" ? data : {};
  }

  function saveUsers(users) {
    saveToStorage(USER_KEY, users);
    window.dispatchEvent(new Event("hedi-users-updated"));
  }

  function loadProviders() {
    const base = (window.HEDI_AUTHZ && window.HEDI_AUTHZ.providers) || {};
    const overrides = loadFromStorage(PROVIDER_KEY, null);
    if (!overrides) return { ...base };
    return { ...base, ...overrides };
  }

  function saveProviders(providers) {
    saveToStorage(PROVIDER_KEY, providers);
  }

  function severityLabel(severity) {
    switch (severity) {
      case 1:
        return "Critical";
      case 2:
        return "High";
      case 3:
        return "Medium";
      case 4:
        return "Low";
      default:
        return "Unrated";
    }
  }

  function severityTone(severity) {
    return `severity-${severity || "pending"}`;
  }

  function formatTimestamp(iso) {
    if (!iso) return "";
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return iso;
    return dt.toLocaleString();
  }

  function renderTickets() {
    const table = document.querySelector("[data-ticket-body]");
    const emptyState = document.querySelector("[data-ticket-empty]");
    if (!table) return;
    const tickets = loadTickets();
    table.innerHTML = "";
    const openTickets = tickets.filter((ticket) => ticket.status !== "resolved");
    if (!openTickets.length) {
      if (emptyState) emptyState.hidden = false;
      return;
    }
    if (emptyState) emptyState.hidden = true;
    openTickets.forEach((ticket) => {
      const row = document.createElement("tr");

      const idCell = document.createElement("td");
      const idCode = document.createElement("code");
      idCode.textContent = ticket.id;
      idCell.appendChild(idCode);
      row.appendChild(idCell);

      const severityCell = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `severity-badge ${severityTone(ticket.severity)}`;
      badge.textContent = severityLabel(ticket.severity);
      severityCell.appendChild(badge);
      row.appendChild(severityCell);

      const submittedCell = document.createElement("td");
      submittedCell.textContent = formatTimestamp(ticket.createdAt);
      row.appendChild(submittedCell);

      const summaryCell = document.createElement("td");
      summaryCell.textContent = ticket.summary || "";
      row.appendChild(summaryCell);

      const actionsCell = document.createElement("td");
      actionsCell.className = "actions";
      const resolveBtn = document.createElement("button");
      resolveBtn.type = "button";
      resolveBtn.className = "ghost";
      resolveBtn.dataset.ticketClose = ticket.id;
      resolveBtn.textContent = "Resolve";
      actionsCell.appendChild(resolveBtn);
      row.appendChild(actionsCell);

      table.appendChild(row);
    });
  }

  function markTicketResolved(id) {
    const tickets = loadTickets();
    const idx = tickets.findIndex((t) => t.id === id);
    if (idx === -1) return;
    tickets[idx].status = "resolved";
    saveTickets(tickets);
    renderTickets();
  }

  async function hashPassword(password) {
    if (!password) return null;
    if (window.crypto && window.crypto.subtle && window.TextEncoder) {
      const buffer = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(password));
      return Array.from(new Uint8Array(buffer))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    }
    return btoa(password);
  }

  async function handleUserForm(event) {
    event.preventDefault();
    const form = event.target;
    const username = form.username.value.trim();
    const password = form.password.value;
    const role = form.role.value;
    const statusEl = document.querySelector("[data-htpasswd-status]");
    if (statusEl) {
      statusEl.hidden = true;
      statusEl.dataset.state = "";
      statusEl.textContent = "";
    }
    if (!username) {
      form.username.focus();
      return;
    }
    if (!ROLE_ORDER.includes(role)) {
      alert("Select a valid role");
      return;
    }
    if (!password) {
      form.password.focus();
      return;
    }
    const users = loadUsers();
    const digest = await hashPassword(password);
    users[username] = {
      role,
      passwordDigest: digest,
      updatedAt: new Date().toISOString(),
    };
    saveUsers(users);
    if (username && !localStorage.getItem(ACTIVE_KEY)) {
      localStorage.setItem(ACTIVE_KEY, username);
    }
    const command = document.querySelector("[data-htpasswd-command]");
    if (command) {
      command.textContent = `htpasswd -B /path/to/portal.htpasswd ${username}`;
    }
    renderUsers();

    try {
      const response = await fetch("/admin/api/htpasswd", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "same-origin",
        body: JSON.stringify({ username, password }),
      });
      let data = null;
      try {
        data = await response.json();
      } catch (err) {
        data = null;
      }
      if (!response.ok || !data || data.ok !== true) {
        let message = (data && (data.error || data.output)) || response.statusText || "htpasswd execution failed";
        if (data && data.hint) {
          message = `${message} — ${data.hint}`;
        }
        throw new Error(message);
      }
      if (statusEl) {
        statusEl.hidden = false;
        statusEl.dataset.state = "success";
        statusEl.textContent = data.message || `htpasswd updated for ${username}.`;
      }
    } catch (err) {
      if (statusEl) {
        statusEl.hidden = false;
        statusEl.dataset.state = "error";
        const baseMessage = err && err.message ? err.message : "command error";
        statusEl.textContent = `htpasswd failed: ${baseMessage}`;
      } else {
        console.warn("htpasswd command failed", err);
      }
    }
    form.reset();
  }

  function renderUsers() {
    const body = document.querySelector("[data-user-body]");
    const emptyState = document.querySelector("[data-user-empty]");
    if (!body) return;
    const users = loadUsers();
    const entries = Object.entries(users);
    body.innerHTML = "";
    if (!entries.length) {
      if (emptyState) emptyState.hidden = false;
      return;
    }
    if (emptyState) emptyState.hidden = true;
    entries
      .sort(([a], [b]) => a.localeCompare(b))
      .forEach(([username, info]) => {
        const row = document.createElement("tr");

        const userCell = document.createElement("td");
        userCell.textContent = username;
        row.appendChild(userCell);

        const roleCell = document.createElement("td");
        const select = document.createElement("select");
        select.dataset.userRole = username;
        ROLE_ORDER.forEach((role) => {
          const option = document.createElement("option");
          option.value = role;
          option.textContent = role;
          if (info.role === role) {
            option.selected = true;
          }
          select.appendChild(option);
        });
        roleCell.appendChild(select);
        row.appendChild(roleCell);

        const updatedCell = document.createElement("td");
        updatedCell.textContent = info.updatedAt ? formatTimestamp(info.updatedAt) : "—";
        row.appendChild(updatedCell);

        const digestCell = document.createElement("td");
        const digestCode = document.createElement("code");
        if (info.passwordDigest) {
          digestCode.textContent = `${info.passwordDigest.slice(0, 12)}…`;
        } else {
          digestCode.textContent = "set via htpasswd";
        }
        digestCell.appendChild(digestCode);
        row.appendChild(digestCell);

        const actionsCell = document.createElement("td");
        actionsCell.className = "actions";
        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "ghost";
        removeBtn.dataset.userRemove = username;
        removeBtn.textContent = "Remove";
        actionsCell.appendChild(removeBtn);
        row.appendChild(actionsCell);

        body.appendChild(row);
      });
  }

  function handleUserTableClick(event) {
    const remove = event.target.closest("[data-user-remove]");
    if (remove) {
      const username = remove.getAttribute("data-user-remove");
      if (confirm(`Remove ${username}?`)) {
        const users = loadUsers();
        delete users[username];
        saveUsers(users);
        if (localStorage.getItem(ACTIVE_KEY) === username) {
          localStorage.removeItem(ACTIVE_KEY);
        }
        renderUsers();
      }
      return;
    }
  }

  function handleRoleChange(event) {
    const select = event.target.closest("select[data-user-role]");
    if (!select) return;
    const username = select.getAttribute("data-user-role");
    const role = select.value;
    if (!ROLE_ORDER.includes(role)) return;
    const users = loadUsers();
    if (!users[username]) return;
    users[username].role = role;
    users[username].updatedAt = new Date().toISOString();
    saveUsers(users);
    renderUsers();
  }

  function renderProviders() {
    const providers = loadProviders();
    document.querySelectorAll("[data-provider-toggle]").forEach((input) => {
      const key = input.getAttribute("data-provider-toggle");
      if (!providers[key]) {
        providers[key] = { enabled: false };
      }
      input.checked = Boolean(providers[key].enabled);
    });
    document.querySelectorAll("[data-provider-status]").forEach((el) => {
      const key = el.getAttribute("data-provider-status");
      const provider = providers[key];
      const enabled = provider && provider.enabled;
      el.textContent = enabled ? "Enabled" : "Disabled";
      el.dataset.state = enabled ? "on" : "off";
    });
  }

  function handleProviderToggle(event) {
    const input = event.target.closest("[data-provider-toggle]");
    if (!input) return;
    const key = input.getAttribute("data-provider-toggle");
    const providers = loadProviders();
    providers[key] = providers[key] || {};
    providers[key].enabled = input.checked;
    saveProviders(providers);
    renderProviders();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("admin-user-form");
    if (form) {
      form.addEventListener("submit", handleUserForm);
    }
    const userTable = document.querySelector("[data-user-table]");
    if (userTable) {
      userTable.addEventListener("click", handleUserTableClick);
      userTable.addEventListener("change", handleRoleChange);
    }
    const ticketTable = document.querySelector("[data-ticket-table]");
    if (ticketTable) {
      ticketTable.addEventListener("click", (event) => {
        const close = event.target.closest("[data-ticket-close]");
        if (close) {
          markTicketResolved(close.getAttribute("data-ticket-close"));
        }
      });
    }
    document.querySelectorAll("[data-provider-toggle]").forEach((input) => {
      input.addEventListener("change", handleProviderToggle);
    });
    renderTickets();
    renderUsers();
    renderProviders();
  });

  window.addEventListener("hedi-ticket-created", renderTickets);
  window.addEventListener("storage", (event) => {
    if (event.key === TICKET_KEY) {
      renderTickets();
    }
    if (event.key === USER_KEY || event.key === ACTIVE_KEY) {
      renderUsers();
    }
    if (event.key === PROVIDER_KEY) {
      renderProviders();
    }
  });
})();
