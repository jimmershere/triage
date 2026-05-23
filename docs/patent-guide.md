# Software Patent Guide: Triage Healthcare EDI Platform

A practical guide for pursuing patent protection on novel systems within the Triage platform. This is not legal advice — it is preparation material for engaging a patent attorney.

---

## 1. What IS Patentable When Building on Open Source

A common misconception: because Triage uses Go, Python, PostgreSQL, and RabbitMQ, there is nothing patentable. This is wrong.

**You cannot patent the tools. You CAN patent what you build with them.**

Patentable subject matter includes:

- **Novel methods** — a new way of processing, routing, or scoring claims that did not exist before
- **Novel systems** — a specific arrangement of components that produces a non-obvious result
- **Novel workflows** — a sequence of automated steps that solves a problem in a new way
- **Novel user interfaces** — a specific visual/interactive approach to presenting or manipulating data (design patent territory)

The implementation language and infrastructure are irrelevant to patentability. What matters is whether your *method* or *system* is novel, non-obvious, and useful.

**Key principle:** The patent covers the *what* and the *how* of your invention, not the *with what* it was built.

---

## 2. What is NOT Patentable

- **Abstract ideas** — "using AI to process claims" is too abstract
- **Obvious combinations** — putting a queue in front of a processor is not novel
- **Mathematical formulas alone** — a scoring algorithm without a specific applied context
- **Business methods without technical implementation** — "the idea of triaging claims by dollar value" without a concrete system
- **Prior art** — anything already published, deployed, or patented by others
- **Laws of nature / natural phenomena**

Under *Alice Corp v. CLS Bank* (2014), software patents must demonstrate something beyond an abstract idea — they need a concrete, technical improvement. Triage's claims are strong here because they describe specific technical systems, not just business concepts.

---

## 3. Three Types of IP Protection Relevant to Triage

### Utility Patent (most valuable here)
- Protects the functional method or system
- Lasts 20 years from filing date
- Requires novelty, non-obviousness, and utility
- Takes 2-4 years to grant
- **Best for:** Tiered AI routing, supervisor verdict system, dollars-at-risk scoring method

### Design Patent
- Protects ornamental appearance of a functional item
- Lasts 15 years from grant date
- Easier and cheaper to obtain
- Narrower protection (only covers the specific visual design)
- **Best for:** Exception command center UI layout, canvas-based segment mapping visual design

### Trade Secret
- No registration required — just keep it secret
- Lasts indefinitely (as long as secrecy is maintained)
- No protection if independently discovered or reverse-engineered
- Requires documented efforts to maintain secrecy (NDAs, access controls, etc.)
- **Best for:** Specific model weights, training data, proprietary scoring coefficients, internal heuristics

**Recommended strategy:** File utility patents on core methods, consider design patents on distinctive UI, maintain trade secrets on tuning parameters and model internals.

---

## 4. Potentially Patentable Inventions in Triage

### Invention 1: Tiered AI Complexity Routing System

**Claim concept:** A computer-implemented method for processing healthcare EDI claims comprising: (a) receiving an inbound claim transaction, (b) computing a complexity score based on [specific factors], (c) classifying the claim into one of four processing tiers based on the score, (d) routing the claim to a tier-appropriate processing pipeline where each tier applies progressively more sophisticated analysis.

**Why it's patentable:** The specific four-tier architecture with defined routing logic, the complexity scoring method, and the automated escalation between tiers constitute a novel system for claims processing. This is not "use AI to process claims" — it is a specific technical architecture.

**Strength:** High. Specific, technical, solves a concrete problem (reducing processing cost while maintaining accuracy).

### Invention 2: Dollars-at-Risk Exception Prioritization Method

**Claim concept:** A system and method for prioritizing healthcare claim exceptions comprising: (a) identifying claims in an exception state, (b) computing a dollars-at-risk score for each exception based on [claim value, aging, payer patterns, denial probability], (c) generating a ranked visual display that surfaces highest-risk exceptions, (d) dynamically re-ranking as claim states change.

**Why it's patentable:** The specific method of computing financial risk for stuck claims and using that to drive operational prioritization is a concrete technical improvement over existing "first-in-first-out" exception queues.

**Strength:** Medium-High. Need to ensure the scoring method is sufficiently specific and non-obvious.

### Invention 3: Canvas-Based X12 Segment Mapping Interface

**Claim concept:** A computer-implemented method for visually mapping healthcare EDI transaction segments comprising: (a) rendering source X12 segments as interactive nodes on a canvas, (b) rendering target schema elements as corresponding nodes, (c) enabling drag-and-drop connection of source to target with real-time validation, (d) generating executable transformation code from the visual mapping.

**Why it's patentable:** Visual programming for X12 mapping with code generation is a specific technical interface that improves over manual coding of EDI transformations. The combination of canvas interaction + X12-specific validation + code generation is non-obvious.

**Strength:** Medium. Similar tools exist in ETL space — novelty argument depends on X12-specific features and validation approach.

### Invention 4: AI-Assisted Supervisor Verdict System with Confidence Scoring

**Claim concept:** A system for adjudicating healthcare claim exceptions comprising: (a) presenting a claim exception to a supervisor interface, (b) generating an AI-recommended verdict (approve/reject/escalate) with an associated confidence score, (c) displaying supporting evidence and similar historical outcomes, (d) recording supervisor override patterns to improve future confidence calibration.

**Why it's patentable:** The feedback loop between AI recommendation, human override, and model calibration — applied specifically to claims adjudication — constitutes a novel human-in-the-loop decision system.

**Strength:** Medium-High. The feedback calibration loop is the strongest novel element.

---

## 5. Step-by-Step: Filing a Provisional Patent Application

A provisional application establishes a priority date (your place in line) for $320 and buys you 12 months to file the full (non-provisional) application.

### Step 1: Document the Invention (Weeks 1-2)
- Write a detailed technical description of each invention
- Include system architecture diagrams
- Describe the problem solved and how your method differs from prior approaches
- Include flowcharts of the method steps
- Document specific examples with real data flows

### Step 2: Conduct a Prior Art Search (Week 3)
- Search USPTO (patents.google.com) for similar claims
- Search academic papers (Google Scholar)
- Search existing products and their documentation
- Document what you found and how your invention differs
- This saves money when you engage an attorney later

### Step 3: Draft the Provisional Application (Weeks 3-4)
- Title
- Cross-reference to related applications (if any)
- Background of the invention (the problem)
- Summary of the invention
- Detailed description with figures
- At least one claim (optional for provisional but strongly recommended)
- Drawings/diagrams (can be informal for provisional)

### Step 4: File with USPTO (Week 4)
- File via USPTO EFS-Web (electronic filing system)
- Pay the fee ($320 for small entity, $160 for micro entity)
- Receive a filing receipt with your priority date
- Mark materials as "Patent Pending"

### Step 5: Use the 12-Month Window (Months 1-12)
- Continue developing the product
- Refine claims based on what proves most valuable
- Engage a patent attorney for the non-provisional filing
- Consider whether to file multiple non-provisionals from one provisional
- File the non-provisional before the 12-month deadline (no extensions possible)

---

## 6. Cost Estimates

| Item | Self-Filed | With Attorney |
|------|-----------|---------------|
| Prior art search | $0-500 | $1,500-3,000 |
| Provisional application | $320 (filing fee) | $2,000-5,000 + $320 fee |
| Non-provisional application | $1,600 (filing fee) | $8,000-15,000 + $1,600 fee |
| Office action responses | N/A | $2,000-4,000 each |
| Total through grant (per patent) | ~$2,000 | $15,000-30,000 |

**Fee category notes:**
- Micro entity (< 5 patents, < $228K gross income): 75% fee reduction
- Small entity (< 500 employees): 50% fee reduction
- Triage likely qualifies as small entity now, possibly micro entity

**Budget recommendation:** Budget $5,000-8,000 per invention for provisional + attorney review. Budget $60,000-120,000 total to take all four inventions through to grant.

---

## 7. Timeline Expectations

| Milestone | Timeframe |
|-----------|-----------|
| Provisional drafting | 2-4 weeks |
| Provisional filing | Day 1 (priority date established) |
| Non-provisional filing deadline | 12 months from provisional |
| USPTO first office action | 12-18 months after non-provisional |
| Responses and amendments | 6-18 months |
| Patent grant | 2-4 years total from non-provisional |

**Note:** You can mark the product "Patent Pending" immediately after filing the provisional. This has deterrent value even before grant.

---

## 8. Key Risks and Considerations

### Alice/101 Rejection Risk
Software patents face Section 101 rejections (abstract idea). Mitigation: Frame claims around specific technical implementations, not business methods. Emphasize the technical problem solved and the technical means of solving it.

### Prior Art Risk
Healthcare EDI processing is a mature field. Mitigation: Focus claims on the specific *combination* and *method* rather than individual components. Your tiered routing with AI scoring is novel; a simple routing table is not.

### Continuation Risk
If you publicly deploy before filing, you start a 1-year clock (in the US only — most foreign jurisdictions require filing before any public disclosure). If you want international protection, file before any public launch.

### Open Source Component Risk
Using OSS does not prevent patenting, but be aware:
- Some OSS licenses (notably Apache 2.0) include patent grants — ensure your claims do not overlap with the OSS components themselves
- Your patents cover your novel methods, not the underlying tools
- Document clearly what is your invention vs. what is commodity infrastructure

### Enforcement Reality
Software patents are expensive to enforce ($1M+ for litigation). Their primary value for a startup is:
- Deterring copycat competitors
- Increasing company valuation
- Licensing revenue potential
- Defensive portfolio (protection from patent trolls via cross-licensing)

---

## 9. Recommended Next Steps

1. **Immediately:** Stop publicly disclosing implementation details of the four inventions above. Put NDAs in place for demos and investor materials that reveal the technical architecture.

2. **This month:** Write detailed invention disclosures for each of the four inventions. Include diagrams, flowcharts, data flows, and specific examples. These become the basis for patent drafts.

3. **Within 60 days:** File at least one provisional patent application — prioritize Invention 1 (Tiered AI Routing) as it is the strongest claim and most central to the platform.

4. **Within 90 days:** Engage a patent attorney who specializes in software/AI patents (not a generalist IP attorney). Ask for experience with healthcare technology and Alice/101 rejections.

5. **Within 6 months:** File provisionals for remaining inventions. Use attorney feedback from Invention 1 to strengthen subsequent filings.

6. **Within 12 months:** Convert the strongest provisionals to non-provisional applications. Drop any that prior art search reveals are weak.

**Attorney selection criteria:**
- Experience with software/AI patents (ask for examples of granted patents)
- Familiarity with Alice/101 issues and how to draft claims that survive
- Healthcare technology experience (helpful but not required)
- Flat-fee or capped-fee arrangements preferred over hourly
- Ask about their office action response success rate

---

## Summary

Triage has at least 3-4 patentable inventions. The strongest candidates are the tiered AI routing system and the supervisor verdict feedback loop. Filing provisional applications is inexpensive and establishes priority dates. The combination of utility patents (methods), design patents (UI), and trade secrets (model internals) provides layered IP protection.

The single most important action is: **file provisional applications before any public disclosure of the technical architecture.**
