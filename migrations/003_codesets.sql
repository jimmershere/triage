-- migrations/003_codesets.sql
-- Workstream 2 — Hot-loadable, effective-dated code-set registry.
--
-- Persists what the in-process CodesetRegistry resolves: a code set, each
-- ingested version (with source-effective / publication / implementation dates
-- and a checksum), and the per-code effective window (valid_from/valid_to) plus
-- active/deactivated status. The loader writes a new version row + values and
-- publishes a codeset.reloaded RabbitMQ event for cache invalidation.

CREATE TABLE IF NOT EXISTS codeset (
  id                  BIGSERIAL PRIMARY KEY,
  name                TEXT NOT NULL UNIQUE,     -- e.g. carc, rarc, icd10cm, icd10pcs
  description         TEXT,
  publisher           TEXT,                     -- ASC X12, CMS, CDC/NCHS, NUCC, ...
  cadence             TEXT,                     -- e.g. '3x/year', 'annual Oct 1'
  x12_source_or_ecl   TEXT,                     -- ECL id / X12 code-source number
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS codeset_version (
  id                    BIGSERIAL PRIMARY KEY,
  codeset_id            BIGINT NOT NULL REFERENCES codeset(id) ON DELETE CASCADE,
  version_label         TEXT NOT NULL,          -- e.g. FY2026, 2025-11-01
  source_effective_date DATE,                   -- when the published set takes effect
  publication_date      DATE,                   -- when the publisher released it
  implementation_date   DATE,                   -- CMS contractor implementation date
  ingest_ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  checksum              TEXT NOT NULL,
  complete              BOOLEAN NOT NULL DEFAULT FALSE,
  CONSTRAINT codeset_version_unique UNIQUE (codeset_id, version_label)
);
CREATE INDEX IF NOT EXISTS codeset_version_effective_idx
  ON codeset_version (codeset_id, source_effective_date);

CREATE TABLE IF NOT EXISTS codeset_value (
  id                  BIGSERIAL PRIMARY KEY,
  codeset_version_id  BIGINT NOT NULL REFERENCES codeset_version(id) ON DELETE CASCADE,
  code                TEXT NOT NULL,
  description         TEXT,
  valid_from          DATE,
  valid_to            DATE,                      -- deactivated-but-historically-valid
  status              TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','deactivated')),
  CONSTRAINT codeset_value_unique UNIQUE (codeset_version_id, code)
);
CREATE INDEX IF NOT EXISTS codeset_value_code_idx
  ON codeset_value (codeset_version_id, code);
