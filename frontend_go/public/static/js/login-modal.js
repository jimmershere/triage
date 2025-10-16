(function () {
  const triggers = Array.from(document.querySelectorAll("[data-login-trigger]"));
  if (!triggers.length) return;

  let overlay = null;
  let form = null;
  let nextInput = null;
  let usernameInput = null;
  let passwordInput = null;

  function ensureModal() {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.className = "hedi-login-overlay";
    overlay.innerHTML = `
      <div class="hedi-login-card" role="dialog" aria-modal="true" aria-labelledby="hediLoginTitle">
        <button type="button" class="hedi-login-close" data-login-close aria-label="Close login">×</button>
        <h2 id="hediLoginTitle">Sign in to HEDI</h2>
        <form class="hedi-login-form" method="post" action="/auth/login">
          <input type="hidden" name="next" value="/portal.html">
          <label>
            <span>Username</span>
            <input type="text" name="username" autocomplete="username" required>
          </label>
          <label>
            <span>Password</span>
            <input type="password" name="password" autocomplete="current-password" required>
          </label>
          <button type="submit">Sign in</button>
        </form>
        <p class="hedi-login-hint">Need portal access? Ask an administrator to add you under Admin → User &amp; role management.</p>
      </div>
    `;
    document.body.appendChild(overlay);
    form = overlay.querySelector("form");
    nextInput = form.querySelector("input[name='next']");
    usernameInput = form.querySelector("input[name='username']");
    passwordInput = form.querySelector("input[name='password']");

    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        closeModal();
      }
    });
    const closeBtn = overlay.querySelector("[data-login-close]");
    if (closeBtn) {
      closeBtn.addEventListener("click", closeModal);
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && overlay.classList.contains("is-open")) {
        closeModal();
      }
    });
  }

  function openModal(nextPath) {
    ensureModal();
    const fallback = "/portal.html";
    const nextValue = typeof nextPath === "string" && nextPath.startsWith("/") ? nextPath : fallback;
    nextInput.value = nextValue;
    overlay.classList.add("is-open");
    document.body.classList.add("hedi-login-locked");
    usernameInput.focus();
    usernameInput.select();
    passwordInput.value = "";
  }

  function closeModal() {
    if (!overlay) return;
    overlay.classList.remove("is-open");
    document.body.classList.remove("hedi-login-locked");
  }

  triggers.forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const nextAttr = trigger.getAttribute("data-login-next") || trigger.getAttribute("href") || "/portal.html";
      openModal(nextAttr.replace(/^[^/]+/, ""));
    });
  });
})();
