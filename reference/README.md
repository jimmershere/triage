# X12 TR3 reference bundle (license-provisioned)

This directory holds the empirically-extracted X12 implementation-guide table
data that drives **table-driven SNIP validation** (see
`worker_py/validation/reference/` and `worker_py/validation/rules/structure.py`).

## Licensing — why the raw data is **not** committed

The raw implementation-guide exports (`source/*-CSV.csv`, `*.pdf`, `*-TD.zip`,
`*-XSD.zip`, and everything under `extracted/`) are **Washington Publishing
Company (WPC) / DISA copyrighted** table data. The WPC license permits importing
this data into a value-added "syntax analyzer or EDI translator" — which is
exactly what Triage is — but **prohibits redistribution** of the guide content
itself.

Accordingly:

- The raw bundle is **git-ignored** (`source/`, `extracted/`). It must be
  provisioned to each deployment under the operator's own WPC license.
- The engine **exposes validation results, never the guide text**. Small X12
  data-element code-value enumerations used for code-set membership are derived
  via `python -m validation.reference.build_codesets` and live under
  `worker_py/validation/codesets/data/`.
- If the bundle is absent, `validation.reference.get_reference()` returns
  `None` and the engine falls back to the hand-coded guide rules — no crash.

## Layout (provisioned, not committed)

```
reference/x12/005010X222/
  source/x222-005010-CSV.csv      # WPC CSV table data (837 Professional)
  source/x222-005010-PDF.pdf
  source/x222-005010-TD.zip
  source/x222-005010-XSD.zip
  extracted/td/.../*.txt          # unzipped WPC variable-length table data
  extracted/xsd/837-Q1A1.xsd
```

Add transactions by dropping their WPC `*-CSV.csv` under
`reference/x12/<implementation-version>/source/` (e.g. `005010X223` for 837I,
`005010X224` for 837D, `005010X220` for 834, `005010X218` for 820). The loader
auto-discovers the CSV; `structure.py` activates that transaction's reference
once a matching `_VERSION_PREFIXES` entry exists.
