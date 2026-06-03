# Triage Demo Data

Synthetic X12 EDI files for demonstrating the Triage Command Center. All data is fictional — no real PHI.

## Files

### demo-837p-clean.edi
- **Type:** 837P Professional Claims
- **Claims:** 15
- **Total Charges:** ~$12,500
- **Status:** All valid, clean file
- **Content:** Mix of office visits (99213, 99214), preventive care (99395), and lab work (80053)
- **Provider:** Summit Family Practice, NPI 1234567890
- **Use:** Baseline demo of successful ingestion, zero exceptions

### demo-837p-exceptions.edi
- **Type:** 837P Professional Claims
- **Claims:** 25
- **Total Charges:** ~$45,000
- **Status:** 6 claims have issues that trigger Tier 3 exceptions
- **Content:** Orthopedic procedures (knee replacement, arthroscopy, shoulder repair)
- **Provider:** Pinnacle Orthopedics Group, NPI 1234598760
- **Exceptions:**
  - EXCL020 (VANCE,XAVIER) — Missing subscriber ID (MI element empty)
  - EXCL021 (WALKER,YVONNE) — Missing subscriber ID (MI element empty)
  - EXCL022 (YOUNG,ALAN) — Invalid procedure code (ZZZZZ)
  - EXCL023 (ZIMMERMAN,BETTY) — Duplicate CLM segment
  - EXCL024 (ABBOTT,CRAIG) — Missing NM1*PR (payer) segment
  - EXCL025 (BENSON,DIANA) — Invalid date format (19910132 — day 32 does not exist)
- **Use:** Demo exception detection, triage queue, and resolution workflow

### demo-837i-institutional.edi
- **Type:** 837I Institutional Claims
- **Claims:** 40
- **Total Charges:** ~$380,000
- **Status:** All valid, clean file
- **Content:** Mix of inpatient stays (rev codes 0120, 0250, 0301) and ED/outpatient (0510, 0636)
- **Provider:** Rocky Mountain Medical Center, NPI 1234500001
- **Use:** Demo high-volume institutional processing, revenue code analytics

### demo-837d-dental.edi
- **Type:** 837D Dental Claims
- **Claims:** 10
- **Total Charges:** ~$4,200
- **Status:** All valid, clean file
- **Content:** Mix of periodic oral evals (D0120), prophylaxis (D1110), resin restorations (D2391)
- **Provider:** Bright Smile Dental Group, NPI 1234511111
- **Use:** Demo dental claim support, ADA procedure code handling

### demo-835-remittance.edi
- **Type:** 835 Electronic Remittance Advice (ERA)
- **Claims Referenced:** 15 (from demo-837p-clean.edi)
- **Payment Amount:** $8,915.50
- **Status:**
  - 12 claims paid (with CO-45 contractual adjustments)
  - 1 claim denied CO-4 (procedure code inconsistent with modifier) — CLAIM006
  - 1 claim denied CO-29 (timely filing limit expired) — CLAIM014
  - 1 claim pending (status 20) — CLAIM015
- **Use:** Demo remittance matching, denial analytics, payment posting

## Delimiters

All files use standard X12 delimiters:
- `*` element separator
- `~` segment terminator
- `:` component separator
- `^` repetition separator (ISA11)

## How to Use

1. **Clean ingestion demo:** Load `demo-837p-clean.edi` to show zero-exception processing
2. **Exception workflow:** Load `demo-837p-exceptions.edi` to populate the triage queue with 6 actionable exceptions
3. **High-volume institutional:** Load `demo-837i-institutional.edi` for throughput and volume metrics
4. **Multi-type demo:** Load all 837 files simultaneously to show mixed claim type handling
5. **End-to-end lifecycle:** Load `demo-837p-clean.edi` then `demo-835-remittance.edi` to demo claim-to-payment matching

## Sender/Receiver IDs

| ID | Role |
|---|---|
| TRIAGETEST | Triage platform (submitter) |
| MEDICARE | CMS Medicare payer |
| DELTADENTALCO | Delta Dental of Colorado |

## Version

All files use 5010 transaction set versions (005010X222A1 for 837P, 005010X223A2 for 837I, 005010X224A2 for 837D, 005010X221A1 for 835).
