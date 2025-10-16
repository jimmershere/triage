# TurboHEDI-0.2 — bots-edi Add‑ons (835, 270/271, 276/277)

This bundle drops into `turbohedi-0.2/` and adds:
- **Grammars** for: 835, 270, 271, 276, 277
- **Mappings**: 835→DB, 271→DB, 277→DB, and a 270/276 **builder** from JSON payloads
- **Routes snippet** you can merge into your bots.ini
- **SQL DDL** for required tables

## Install

1. Unzip at the project root so files land under `turbohedi-0.2/`.
2. Merge `bots/config/routes_x12_addons.ini` into your main `bots.ini` (or include it).
3. Create tables:
   ```sh
   psql "$HEDI_PG_DSN" -f db/hedi_x12_addons.sql
   psql "$HEDI_PG_DSN" -f db/hedi_partner_profiles.sql
   ```
4. Ensure env var `HEDI_PG_DSN` points at your DB (defaults to `postgresql://hedi:hedi@localhost:5432/hedi`).

## Queues expected

- Inbound: `x12.835.in`, `x12.271.in`, `x12.277.in`
- Outbound build: `x12.270.out`, `x12.276.out`

## Example builder payloads

**270** (minimum payload — partner defaults fill everything else)
```json
{"target_st":"270","partner_key":"default","subscriber_id":"S12345","dos":"20250120"}
```

**276** (optionally include `claim_id` or override payer/provider IDs)
```json
{"target_st":"276","partner_key":"default","subscriber_id":"S12345","claim_id":"C-001","dos":"20250120"}
```

## Notes
- These grammars are lean and pragmatic; extend `recorddefs` and `structure` if your payers use optional segments.
- Mappings use lightweight inserts; swap to UPSERT/MERGE patterns or staging tables as needed for high throughput.
- Partner defaults come from the `partner_profiles` table (see `db/hedi_partner_profiles.sql`); payload fields override any defaults.
- Envelope/partners are handled by bots via your partner settings; GS08/versions already match the common HIPAA guides.
- Python 3.12 tested in our pipeline; make sure `psycopg` (v3) is installed in the bots runtime.
