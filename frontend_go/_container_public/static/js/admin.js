(function () {
  const TICKET_KEY = "hediSupportTickets";
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

  function renderProviders() {
    const providers = loadProviders();
    document.querySelectorAll("[data-provider-toggle]").forEach((toggle) => {
      const key = toggle.getAttribute("data-provider-toggle");
      const state = providers[key];
      const enabled = state && state.enabled;
      toggle.checked = Boolean(enabled);
      const card = toggle.closest(".provider-card");
      if (card) {
        const badge = card.querySelector("[data-provider-status]");
        if (badge) {
          badge.textContent = enabled ? "Enabled" : "Disabled";
          badge.dataset.state = enabled ? "enabled" : "disabled";
        }
      }
    });
  }

  function handleProviderToggle(event) {
    const input = event.target;
    if (!input.matches("[data-provider-toggle]")) return;
    const key = input.getAttribute("data-provider-toggle");
    const providers = loadProviders();
    providers[key] = providers[key] || {};
    providers[key].enabled = input.checked;
    saveProviders(providers);
    renderProviders();
  }

  const statusEl = document.querySelector("[data-user-status]");
  const form = document.querySelector("[data-user-form]");
  const cancelBtn = document.querySelector("[data-user-cancel]");
  const submitBtn = document.querySelector("[data-user-submit]");
  const tableBody = document.querySelector("[data-user-body]");
  const emptyState = document.querySelector("[data-user-empty]");

  let editingUser = null;
  let statusTimer = null;

  function setStatus(message, state = "info") {
    if (!statusEl) return;
    if (statusTimer) {
      clearTimeout(statusTimer);
      statusTimer = null;
    }
    if (!message) {
      statusEl.hidden = true;
      statusEl.textContent = "";
      statusEl.dataset.state = "";
      return;
    }
    statusEl.hidden = false;
    statusEl.dataset.state = state;
    statusEl.textContent = message;
    if (state === "success") {
      statusTimer = window.setTimeout(() => {
        if (statusEl.dataset.state === "success") {
          setStatus("", "info");
        }
      }, 4000);
    }
  }

  function resetForm() {
    if (!form) return;
    form.reset();
    form.username.removeAttribute("disabled");
    form.password.required = true;
    form.confirm.required = true;
    editingUser = null;
    submitBtn.textContent = "Create user";
    if (cancelBtn) cancelBtn.hidden = true;
    form.password.value = "";
    form.confirm.value = "";
    if (form.allow_portal) form.allow_portal.checked = true;
    if (form.allow_admin) form.allow_admin.checked = false;
  }

  function fillForm(user) {
    if (!form) return;
    editingUser = user.username;
    form.username.value = user.username;
    form.username.setAttribute("disabled", "disabled");
    form.role.value = user.role;
    if (form.allow_portal) form.allow_portal.checked = Boolean(user.allow_portal);
    if (form.allow_admin) form.allow_admin.checked = Boolean(user.allow_admin);
    form.password.value = "";
    form.confirm.value = "";
    form.password.required = false;
    form.confirm.required = false;
    submitBtn.textContent = "Update user";
    if (cancelBtn) cancelBtn.hidden = false;
  }

  function describeAccess(user) {
    const bits = [];
    if (user.allow_portal) bits.push("Portal");
    if (user.allow_admin) bits.push("Admin");
    if (!bits.length) bits.push("None");
    return bits.join(", ");
  }

  async function loadUsers() {
    if (!tableBody) return;
    try {
      setStatus("Loading users…", "info");
      const response = await fetch("/admin/api/users", { credentials: "same-origin" });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      const data = await response.json();
      const users = (data && data.users) || [];
      tableBody.innerHTML = "";
      if (!users.length) {
        if (emptyState) emptyState.hidden = false;
        setStatus("No users found. Create one to get started.", "info");
        return;
      }
      if (emptyState) emptyState.hidden = true;
      users.forEach((user) => {
        const row = document.createElement("tr");
        row.dataset.username = user.username;

        const nameCell = document.createElement("td");
        const code = document.createElement("code");
        code.textContent = user.username;
        nameCell.appendChild(code);
        row.appendChild(nameCell);

        const roleCell = document.createElement("td");
        roleCell.textContent = user.role;
        row.appendChild(roleCell);

        const accessCell = document.createElement("td");
        accessCell.textContent = describeAccess(user);
        row.appendChild(accessCell);

        const updatedCell = document.createElement("td");
        updatedCell.textContent = formatTimestamp(user.updated_at);
        row.appendChild(updatedCell);

        const actionsCell = document.createElement("td");
        actionsCell.className = "actions";
        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "ghost";
        editBtn.dataset.userEdit = user.username;
        editBtn.textContent = "Edit";
        actionsCell.appendChild(editBtn);

        const deleteBtn = document.createElement("button");
        deleteBtn.type = "button";
        deleteBtn.className = "ghost danger";
        deleteBtn.dataset.userDelete = user.username;
        deleteBtn.textContent = "Delete";
        actionsCell.appendChild(deleteBtn);

        row.appendChild(actionsCell);
        tableBody.appendChild(row);
      });
      if (!statusEl || statusEl.dataset.state !== "success") {
        setStatus("", "info");
      }
    } catch (err) {
      console.error("Failed to load users", err);
      setStatus(err.message || "Unable to load users", "error");
    }
  }

  async function createUser(payload) {
    const response = await fetch("/admin/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || response.statusText);
    }
    return response.json();
  }

  async function updateUser(username, payload) {
    const response = await fetch(`/admin/api/users/${encodeURIComponent(username)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || response.statusText);
    }
    return response.json();
  }

  async function deleteUser(username) {
    const response = await fetch(`/admin/api/users/${encodeURIComponent(username)}`, {
      method: "DELETE",
      credentials: "same-origin",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || response.statusText);
    }
    return response.json();
  }

  async function handleFormSubmit(event) {
    event.preventDefault();
    if (!form) return;
    const username = form.username.value.trim();
    const role = form.role.value;
    const allowPortal = form.allow_portal ? form.allow_portal.checked : false;
    const allowAdmin = form.allow_admin ? form.allow_admin.checked : false;
    const password = form.password.value;
    const confirm = form.confirm.value;

    if (!username) {
      form.username.focus();
      return;
    }
    if (!ROLE_ORDER.includes(role)) {
      setStatus("Select a valid role", "error");
      return;
    }
    if (!allowPortal && !allowAdmin) {
      setStatus("Choose at least one access area", "error");
      return;
    }

    try {
      if (editingUser) {
        if (password && password !== confirm) {
          setStatus("Passwords do not match", "error");
          return;
        }
        const payload = {
          role,
          allow_portal: allowPortal,
          allow_admin: allowAdmin,
        };
        if (password) {
          payload.password = password;
        }
        setStatus("Updating user…", "info");
        await updateUser(editingUser, payload);
        setStatus(`Updated ${editingUser}`, "success");
      } else {
        if (!password || password.length < 8) {
          setStatus("Password must be at least 8 characters", "error");
          return;
        }
        if (password !== confirm) {
          setStatus("Passwords do not match", "error");
          return;
        }
        setStatus("Creating user…", "info");
        await createUser({
          username,
          password,
          role,
          allow_portal: allowPortal,
          allow_admin: allowAdmin,
        });
        setStatus(`Created ${username}`, "success");
      }
      resetForm();
      await loadUsers();
      window.dispatchEvent(new Event("hedi-role-refresh"));
    } catch (err) {
      console.error("User save failed", err);
      setStatus(err.message || "Unable to save user", "error");
    }
  }

  function handleTableClick(event) {
    const target = event.target;
    if (!tableBody || !target) return;
    if (target.matches("[data-user-edit]")) {
      const username = target.getAttribute("data-user-edit");
      const row = target.closest("tr");
      if (!row) return;
      const role = row.children[1]?.textContent || "view";
      const access = (row.children[2]?.textContent || "").toLowerCase();
      const allowPortal = access.includes("portal");
      const allowAdmin = access.includes("admin");
      fillForm({ username, role, allow_portal: allowPortal, allow_admin: allowAdmin });
      setStatus(`Editing ${username}`, "info");
    } else if (target.matches("[data-user-delete]")) {
      const username = target.getAttribute("data-user-delete");
      if (!username) return;
      if (!window.confirm(`Delete ${username}?`)) return;
      deleteUser(username)
        .then(() => {
          setStatus(`Deleted ${username}`, "success");
          if (editingUser === username) {
            resetForm();
          }
          return loadUsers();
        })
        .then(() => {
          window.dispatchEvent(new Event("hedi-role-refresh"));
        })
        .catch((err) => {
          console.error("Delete failed", err);
          setStatus(err.message || "Unable to delete user", "error");
        });
    }
  }

  if (form) {
    form.addEventListener("submit", handleFormSubmit);
  }
  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      resetForm();
      setStatus("", "info");
    });
  }
  if (tableBody) {
    tableBody.addEventListener("click", handleTableClick);
  }

  document.addEventListener("click", handleProviderToggle);

  document.addEventListener("DOMContentLoaded", () => {
    renderTickets();
    renderProviders();
    loadUsers();
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (target && target.matches("[data-ticket-close]")) {
      const id = target.getAttribute("data-ticket-close");
      markTicketResolved(id);
    }
  });

  window.addEventListener("hedi-ticket-created", (event) => {
    if (!event || !event.detail) return;
    const tickets = loadTickets();
    tickets.unshift(event.detail);
    saveTickets(tickets);
    renderTickets();
  });
})();
