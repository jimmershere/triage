# Triage — Patent Evidence Map

Maps candidate invention areas from the private patent strategy materials to
the concrete code, test fixtures and rule data that demonstrate them. The
strategy document itself is not committed to this repository. This map is used to
back the technical specification of any provisional / non-provisional filing
and to keep architectural intent visible to future contributors.

---

## §4A — Tiered AI Routing System (Tier 0-3)

> "A computer-implemented method for processing healthcare EDI claims
> comprising a multi-tiered routing system wherein claims are dynamically
> classified and routed through progressively complex processing tiers based
> on specific technical criteria."

### Implementing modules
| Layer | File | Role |
|---|---|---|
| Deterministic profile | `worker_py/file_profiler.py` | Cheap, deterministic metadata extraction (segment density, transaction-set mix, partner tier, control-number integrity, anomaly markers). |
| Weighted scoring | `worker_py/complexity_scorer.py` | 100-point weighted score across structural load, novelty, execution risk and business impact. Emits factor-by-factor evidence. |
| Routing engine | `worker_py/routing_engine.py` | Threshold bands + configurable hard escalation gates (low confidence, new partner, multi-anomaly, compliance materiality). |
| Tier 0 fast path | `worker_py/tier_executor.py` (`_execute_tier0`) | Deterministic translator pipeline, no AI. |
| Tier 1 assisted review | `worker_py/tier1_assist.py` | Enhanced deterministic checks (duplicates, outliers, segment density, control structure). |
| Tier 2 supervised swarm | `worker_py/tier2_swarm.py` and `worker_py/swarms/runner.py` (new framework) | Parallel specialist agents + supervisor verdict. |
| Tier 3 human exception | `worker_py/tier_executor.py` (`_execute_tier3`) | Parks the file with structured audit reasoning. |
| Persistence | `worker_py/worker.py` (`ensure_routing_tables`, `process_payload`) | `routing_decisions` and `tier_executions` tables. |

### Test coverage proving the architecture works
- `worker_py/test_routing_mvp.py` — 17 tests covering scorer bands, gate triggers, decision traceability.
- `worker_py/test_tier_execution.py` — full pipeline (profile → score → route → execute) per tier.
- `worker_py/swarms/test_swarms.py` — generalized swarm framework with mock LLM, retries, audit trail.

### Technical-improvement claim hooks
- **Reduced computational load** — Tier 0 routes the bulk of conforming claims with zero AI invocation; only Tier 2+ engages the LLM swarm.
- **Confidence-weighted escalation** — every routing decision carries a confidence score and a list of gate triggers, recorded in the `routing_decisions` table for replay.
- **Feedback signals** — `FileProfile` exposes `historical_failure_rate` and `retry_count` so Tier outcomes can re-train the scorer.

---

## §4B — Visual X12 Segment Canvas Mapper

> "A graphical user interface system for healthcare EDI document analysis
> comprising a canvas-based visual representation of X12 transaction segments
> with integrated issue detection indicators and contextual guidance overlays."

### Implementing modules
| Layer | File | Role |
|---|---|---|
| Canvas UI | `frontend_go/public/static/js/mapper.js` | Renders segments as a coloured tree; XML-style transaction-set header auto-adjusts on `ST*837P`/`837I`/`837D`. Lightweight rules flag identifiers, segment-IDs and missing `~` terminators. |
| Thought-bubble guidance | `frontend_go/public/static/js/mapper.js` + `frontend_go/public/static/css/styles.css` | Light-blue inline bubbles toggle a contextual explanation (uses `trish-laptop.svg`). One-at-a-time interaction model. |
| Backend issue location | `worker_py/validation/model.py` (`ValidationIssue.segment_position`, `element_position`, `component_position`, `loop_id`) | Every validator issue carries the exact (segment, element, component, loop) anchor so the canvas can highlight precisely. |
| Issue stream | `worker_py/validation/engine.py` (`ValidationReport.issues`) | Single ordered list with stable rule codes (`REQ.*`, `BAL.*`, `SIT.*`, `CODE.*`, `GUIDE.*`). |

### Test coverage
- `worker_py/validation/test_parser.py` — segment positions are sequential and 1-based per transaction.
- `worker_py/validation/test_engine.py` — every rule emits an issue with the precise anchor used by the canvas.

### Technical-improvement claim hooks
- **Positioned issue reporting** — the validation engine emits machine-readable anchors so the UI can highlight on the canvas without parsing free-text errors.
- **Standard-aware visual variants** — the canvas adapts when the transaction is institutional vs dental vs professional, matching the rules surfaced by `validation.rules.guide_837`.

---

## §4C — Exception Command Center Dashboard

> "A computer-implemented system for healthcare claims exception management
> comprising a real-time aggregation engine that computes financial risk
> metrics from pending exception queues."

### Implementing modules
| Layer | File | Role |
|---|---|---|
| Aggregation endpoint | `api/app.py` (`/ops/command-center`, `/ops/alerts`) | Real-time aggregation over the imports/claims/acks tables. |
| Dollars-at-risk | `worker_py/scrubbing/model.py` (`ScrubReport.deny_findings`) + `validation.model.ClaimProjection.total_charge` | Per-claim DENY findings × total charge yields the financial exposure metric. |
| Severity stratification | `scrubbing.ScrubSeverity` (`DENY` / `REVIEW` / `ADVISORY`) | Three-tier surfacing matched to the operator / analyst / executive views (§4E). |
| Category breakdown | `ScrubReport.category_summary()` | Per-edit-type counts (`ncci_ptp`, `ncci_mue`, `coverage`, ...) drive the dashboard tiles. |

### Test coverage
- `worker_py/scrubbing/test_scrubbing.py` — category summary, severity flags, report serialization to JSON for the dashboard.
- `worker_py/test_turbo_api.py` — `/turbo/pipeline` returns combined validation + scrubbing summary suitable for command-center rendering.

---

## §4D — AI-Guided Fix Suggestion Workflow

> "A method for automated remediation suggestion in healthcare EDI claim
> processing, comprising: receiving a rejected claim with denial codes;
> correlating denial codes with historical resolution patterns; generating
> ranked fix suggestions using a trained model; and presenting suggested
> fixes in an interactive workflow that enables single-action claim
> resubmission."

### Implementing modules
| Layer | File | Role |
|---|---|---|
| Deterministic resolutions | `worker_py/scrubbing/edits/*.py` (`ScrubFinding.resolution`) | Every CMS payment edit emits a concrete, machine-readable remediation suggestion alongside the finding. |
| Denial-resolution swarm | `worker_py/swarms/denial_swarm.py` | Four specialist agents (reason interpretation, fix proposal, documentation, appeal narrative) + supervisor verdict with actions. |
| Pre-submission scrubbing swarm | `worker_py/swarms/scrubbing_swarm.py` | Four agents review a flagged claim (coding correctness, medical necessity, NCCI bundling, demographics) → APPROVE/FLAG/REJECT for resubmission. |
| Reusable supervisor logic | `worker_py/swarms/supervisor.py` | Tolerant JSON verdict parser with safe `FLAG` fallback so the workflow never deadlocks on an LLM hiccup. |
| Audit trail | `worker_py/swarms/runner.py` (`SwarmResult.audit`) | Every prompt, response, retry and timing recorded for compliance replay. |

### Test coverage
- `worker_py/swarms/test_swarms.py` — workload swarms produce verdicts and actions; supervisor parser tolerates malformed input; retries recover from transient failures.

---

## §4E — Multi-View Architecture (Operator / Analyst / Executive)

> "Same real-time data stream, three distinct cognitive models, with
> contextual actions appropriate to each role."

### Implementing modules
| Role | Data source | Surface |
|---|---|---|
| Operator (claim-level) | `ValidationReport.issues_for_claim`, `ScrubReport.findings_for_claim` | Per-claim drill-down with the exact element ref to fix. |
| Analyst (queue-level) | `ValidationReport.snip_summary`, `ScrubReport.category_summary` | Aggregate severity counts by SNIP type and CMS edit category. |
| Executive (financial) | `ScrubReport.deny_findings` × `ClaimProjection.total_charge` | Dollars-at-risk roll-up surfaced by `/ops/command-center`. |

The single underlying data model (`ValidationReport` + `ScrubReport`) services all three views, so the views never diverge.

---

## §6+ — Trade-secret protected components

Aspects of the implementation that are **deliberately not specified here**
(per the patent strategy's trade-secret recommendations):
- Specific weight constants in `complexity_scorer._DEFAULT_WEIGHTS`.
- Trading-partner reliability classifications maintained outside the repo.
- Prompts engineered for the supervisor and per-agent calls (`swarms/scrubbing_swarm._AGENTS`, etc.) — protected as trade-secret training/prompting know-how.
