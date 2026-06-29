# X222 005010 research bundle for TurboHEDI

Filed: 2026-03-22
Source: Jimmer download via Glass license

## What was downloaded
- `source/x222-005010-CSV.csv`
- `source/x222-005010-PDF.pdf`
- `source/x222-005010-TD.zip`
- `source/x222-005010-XSD.zip`

## What was extracted
### Table Data zip
Extracted to:
- `extracted/td/005010X222 Health Care Claim Professional/`

Contains the implementation/table-data text files needed for machine import into an EDI translator or analyzer, including:
- `sethead.txt`
- `setdetl.txt`
- `seghead.txt`
- `segdetl.txt`
- `comhead.txt`
- `comdetl.txt`
- `elehead.txt`
- `eledetl.txt`
- `condetl.txt`
- `context.txt`
- `freeform.txt`
- `readme.txt`

Per `readme.txt`, this is specifically licensed for import into a value-added translator/analyzer product and is more directly useful for code-update work than browsing the rendered site.

### XSD zip
Extracted to:
- `extracted/xsd/837-Q1A1.xsd`

This provides a structured schema representation of the 837 Health Care Claim transaction and loop/segment hierarchy.

## Initial assessment
For TurboHEDI code-update research, the downloaded materials appear sufficient for most offline engineering work:
- CSV: quick searchable flat reference
- PDF: human-readable guide/reference
- Table data TXT set: best source for parser/import/mapping logic
- XSD: structural schema reference

## Do we still need the website?
Probably **not for the core code-update work**, unless we need one of these later:
- cross-check against a newer corrigendum/version online
- inspect website-only metadata, errata, or navigation aids
- verify whether the downloaded package is missing supplemental notes

## Recommendation
Use the downloaded docs as the primary research corpus for TurboHEDI updates.
Only revisit `https://x222-005010.x12.org/` if we hit a gap not covered by:
- `context.txt`
- `condetl.txt`
- `freeform.txt`
- the PDF
- the XSD
