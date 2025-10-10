
// Minimal client-side JS. Replace fetch endpoints with your backend.
window.APP_CONFIG = window.APP_CONFIG || {
  apiBase: "/api",
  portalName: "Sassy Medicare Claims",
  aaaProvider: "Keycloak (OIDC)",
};

function $(q){ return document.querySelector(q); }

function toast(msg, type="info"){
  const el = document.createElement("div");
  el.role = "status";
  el.style.cssText = "position:fixed;right:16px;bottom:16px;background:#111;color:#fff;padding:12px 14px;border-radius:12px;opacity:.96;z-index:9999";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(()=>{ el.remove(); }, 2800);
}

// Demo: metrics, not real-time
function loadKPIs(){
  const kpis = [
    {label:"Claims Today", val: "1,284"},
    {label:"Avg. Latency", val: "210ms"},
    {label:"Success Rate", val: "99.98%"},
    {label:"Active Users", val: "3,492"},
  ];
  const wrap = document.getElementById("kpis");
  if(!wrap) return;
  wrap.innerHTML = kpis.map(k => `
    <div class="kpi">
      <div class="value">${k.val}</div>
      <div class="muted">${k.label}</div>
    </div>`).join("");
}

// Fake “submit claim” handler
async function submitClaim(e){
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  try {
    // Replace with POST to backend
    console.log("Submitting claim", data);
    toast("✨ Claim submitted (demo)", "success");
    e.target.reset();
  } catch (err){
    toast("Submission failed (demo)", "error");
  }
}

document.addEventListener("DOMContentLoaded", ()=>{
  loadKPIs();
  const form = document.getElementById("claim-form");
  if (form) form.addEventListener("submit", submitClaim);
});
