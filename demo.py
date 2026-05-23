"""TurboHEDI laptop demo bootstrap.

Stands up a minimal FastAPI app on http://127.0.0.1:8765 that mounts only the
``/turbo/*`` routes, serves the frontend static assets, and presents a single
demo page (``/``) preloaded with sample X12 transactions.

No Postgres, RabbitMQ, LDAP, Go frontend or other docker-compose services are
required — everything happens in-process through the .venv interpreter.

Run::

    .venv/bin/python demo.py            # blocks
    .venv/bin/uvicorn demo:app          # equivalent, uses uvicorn directly

Stop with Ctrl-C (or kill the background process).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "api"))
sys.path.insert(0, str(_ROOT / "worker_py"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from turbo_routes import register as register_turbo_routes  # noqa: E402
from validation._fixtures import (  # noqa: E402
    VALID_270,
    VALID_271,
    VALID_276,
    VALID_277,
    VALID_278,
    VALID_835,
    VALID_837D,
    VALID_837I,
    VALID_837P,
    with_replacement,
    with_unbalanced_claim,
)

# ---------------------------------------------------------------------------
# Sample fixtures the demo page exposes via dropdown / buttons.
# ---------------------------------------------------------------------------

_BROKEN_837P_MISSING_HI = VALID_837P.replace("HI*ABK:E119~", "")
_BROKEN_837P_BAD_NPI = VALID_837P.replace("XX*1234567893", "XX*1234567890")
_BROKEN_837P_NCCI = VALID_837P.replace(
    "SV1*HC:99213*100*UN*1***1",
    "SV1*HC:99214*175*UN*1***1",
).replace(
    "SV1*HC:85025*50*UN*1***1",
    "SV1*HC:27447*2675*UN*1***1",
).replace(
    "CLM*CLAIM001*150",
    "CLM*CLAIM001*2850",
)

SAMPLES: dict[str, dict[str, str]] = {
    "valid_837p":             {"label": "Valid 837P (Professional)",         "x12": VALID_837P},
    "valid_837i":             {"label": "Valid 837I (Institutional)",        "x12": VALID_837I},
    "valid_837d":             {"label": "Valid 837D (Dental)",               "x12": VALID_837D},
    "valid_835":              {"label": "Valid 835 (Remittance)",            "x12": VALID_835},
    "valid_270":              {"label": "270 Eligibility Inquiry",           "x12": VALID_270},
    "valid_271":              {"label": "271 Eligibility Response",          "x12": VALID_271},
    "valid_276":              {"label": "276 Claim Status Request",          "x12": VALID_276},
    "valid_277":              {"label": "277 Claim Status Response",         "x12": VALID_277},
    "valid_278":              {"label": "278 Prior Auth",                    "x12": VALID_278},
    "broken_837p_missing_hi": {"label": "BROKEN: 837P missing diagnosis",    "x12": _BROKEN_837P_MISSING_HI},
    "broken_837p_unbalanced": {"label": "BROKEN: 837P CLM02 vs lines",       "x12": with_unbalanced_claim()},
    "broken_837p_bad_npi":    {"label": "BROKEN: 837P invalid NPI Luhn",     "x12": _BROKEN_837P_BAD_NPI},
    "broken_837p_replacement":{"label": "BROKEN: 837P replacement w/o REF*F8","x12": with_replacement()},
    "ncci_837p":              {"label": "Triggers NCCI PTP bundling edit",   "x12": _BROKEN_837P_NCCI},
}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="TurboHEDI demo", version="0.3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
register_turbo_routes(app)

# Mount the existing frontend assets for the panel's stylesheet + JS.
_STATIC = _ROOT / "frontend_go" / "public" / "static"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/demo/samples")
def list_samples() -> dict[str, dict[str, str]]:
    return SAMPLES


DEMO_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TurboHEDI v0.3 demo</title>
  <link rel="stylesheet" href="/static/css/turbo-validate.css" />
  <style>
    :root {
      --ink: #1f2a36;
      --muted: #5d6b7a;
      --bg: #f5f7fa;
      --card: #ffffff;
      --accent: #2a6fb4;
      --border: #d6dde7;
    }
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      margin: 0;
      background: var(--bg);
      color: var(--ink);
    }
    header.demo-header {
      background: linear-gradient(135deg, #1d4f7a, #2a6fb4);
      color: #fff;
      padding: 28px 32px;
    }
    header.demo-header h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: 0.2px; }
    header.demo-header p  { margin: 0; opacity: 0.92; font-size: 14px; }
    main { max-width: 1100px; margin: 24px auto; padding: 0 24px 64px; }
    .demo-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 18px;
      box-shadow: 0 1px 3px rgba(20, 30, 40, 0.04);
    }
    .demo-card h2 { margin: 0 0 8px; font-size: 16px; }
    .demo-card p  { margin: 0 0 12px; color: var(--muted); font-size: 13px; }
    .sample-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 8px;
    }
    .sample-btn {
      text-align: left;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fbfcfe;
      cursor: pointer;
      font: inherit;
      color: var(--ink);
    }
    .sample-btn:hover  { background: #eef3f9; border-color: var(--accent); }
    .sample-btn.broken { background: #fdf4f4; border-color: #e8b4b4; }
    .sample-btn.broken:hover { background: #fbe7e7; }
    .endpoints {
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 6px 14px;
      font-size: 13px;
    }
    .endpoints code {
      background: #f1f5fa;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 12px;
    }
    .pill-button {
      background: var(--accent);
      color: #fff;
      border: none;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 13px;
      cursor: pointer;
    }
    .pill-button:hover { background: #1d4f7a; }
    .pill-button.ghost { background: #ffffff; color: var(--accent); border: 1px solid var(--accent); }
    .pill-button.ghost:hover { background: #eef3f9; }
    .panel-section-title { margin: 0 0 8px; font-size: 16px; }
  </style>
</head>
<body>
  <header class="demo-header">
    <h1>TurboHEDI v0.3 demo</h1>
    <p>SNIP 1-7 validation, CMS payment edits, FHIR R4 round-trip and supervised swarms — running locally in-process.</p>
  </header>
  <main>
    <section class="demo-card">
      <h2>1. Load a sample transaction</h2>
      <p>Each button pastes a representative X12 payload into the panel below. The "BROKEN" variants are designed to fire specific validators / edits.</p>
      <div class="sample-grid" id="sampleGrid"></div>
    </section>

    <section class="demo-card turbo-validate-panel" data-turbo-validate>
      <h2 class="panel-section-title">2. Run CMS validation</h2>
      <p class="turbo-validate-hint">
        <strong>Run validation</strong> covers WEDI SNIP types 1-7 (integrity,
        requirement, balancing, situational, code set, line balancing,
        implementation guide). <strong>Full pipeline</strong> also runs the
        scrubbing engine — NCCI PTP / MUE, NCD/LCD, modifier and demographic
        edits, duplicate detection and eligibility on the date of service.
      </p>
      <textarea class="turbo-validate-input" id="turboInput" placeholder="ISA*00*..." spellcheck="false"></textarea>
      <div class="turbo-validate-actions">
        <button type="button" class="pill-button"        data-turbo-validate-run>Run validation</button>
        <button type="button" class="pill-button ghost"  data-turbo-validate-pipeline>Full pipeline</button>
      </div>
      <div class="turbo-validate-results" data-turbo-validate-results aria-live="polite"></div>
    </section>

    <section class="demo-card">
      <h2>3. Explore the API directly</h2>
      <p>Every demo action is also a plain HTTP call you can curl or hit in a browser.</p>
      <div class="endpoints">
        <code>GET /turbo/capability</code>
        <span><a href="/turbo/capability" target="_blank">view JSON</a> — capability statement (supported transactions, SNIP levels, IGs)</span>
        <code>POST /turbo/validate</code>
        <span>SNIP 1-7 validation; body <code>{"x12": "..."}</code></span>
        <code>POST /turbo/pipeline</code>
        <span>validate -> scrub -> (FHIR) -> (acks); body <code>{"x12":"...", "scrub":true, "to_fhir":true, "generate_acks":true}</code></span>
        <code>POST /turbo/fhir/from-x12</code>
        <span>X12 -> FHIR Bundle (Patient + Organization + Claim or EOB)</span>
        <code>POST /turbo/fhir/Claim/$submit</code>
        <span>FHIR Claim -> X12 837 (round-trip)</span>
        <code>GET /docs</code>
        <span><a href="/docs" target="_blank">Swagger UI</a> — interactive API explorer</span>
      </div>
    </section>
  </main>

  <script src="/static/js/turbo_validate.js"></script>
  <script>
    (async function loadSamples() {
      const grid = document.getElementById("sampleGrid");
      const input = document.getElementById("turboInput");
      try {
        const r = await fetch("/demo/samples");
        const samples = await r.json();
        const entries = Object.entries(samples);
        for (const [key, sample] of entries) {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "sample-btn" + (sample.label.startsWith("BROKEN") ? " broken" : "");
          b.textContent = sample.label;
          b.addEventListener("click", () => {
            input.value = sample.x12;
            input.scrollIntoView({ behavior: "smooth", block: "center" });
          });
          grid.appendChild(b);
        }
        // Preload the clean 837P so the page is immediately interactive.
        if (samples.valid_837p) input.value = samples.valid_837p.x12;
      } catch (err) {
        grid.textContent = "Could not load samples: " + err;
      }
    })();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def demo_index() -> str:
    return DEMO_HTML


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
