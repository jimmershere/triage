-- migrations/002_partners.sql
-- Workstream 6 — Submitter / Trading Partner Management.
--
-- Additive, normalized trading-partner model. This is the single source of
-- truth that the code-set registry (WS2) and the SNIP/ack toggle engine (WS7)
-- read from. It does NOT replace or alter the legacy db/triage_partner_profiles
-- (partner_profiles) or api/app.py's partner_configs table — both remain.

-- Core identity ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner (
  id           BIGSERIAL PRIMARY KEY,
  external_id  TEXT NOT NULL UNIQUE,           -- stable id for idempotent upserts
  name         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active','inactive','suspended','test')),
  notes        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ISA/GS qualifier + id pairs (sender/receiver, test/prod) -----------------
CREATE TABLE IF NOT EXISTS partner_identifier (
  id                     BIGSERIAL PRIMARY KEY,
  partner_id             BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
  interchange_qualifier  TEXT NOT NULL,         -- ISA05/ISA07 (ZZ, 01, 30, ...)
  interchange_id         TEXT NOT NULL,         -- ISA06/ISA08 (15-char padded)
  application_id         TEXT,                  -- GS02/GS03
  direction              TEXT NOT NULL CHECK (direction IN ('sender','receiver')),
  usage                  TEXT NOT NULL DEFAULT 'P' CHECK (usage IN ('P','T')),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- A given qualifier+id, in a given direction + production/test mode, must map
  -- to exactly one partner. This catches the frequent silent-rejection causes:
  -- duplicate/colliding ISA ids and test-vs-prod identifier mix-ups.
  CONSTRAINT partner_identifier_unique
    UNIQUE (interchange_qualifier, interchange_id, direction, usage)
);
CREATE INDEX IF NOT EXISTS partner_identifier_partner_idx
  ON partner_identifier (partner_id);

-- Enabled transaction types + direction ------------------------------------
CREATE TABLE IF NOT EXISTS partner_transaction (
  id                BIGSERIAL PRIMARY KEY,
  partner_id        BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
  transaction_type  TEXT NOT NULL,             -- 837P, 837I, 837D, 835, 270, ...
  direction         TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
  enabled           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT partner_transaction_unique
    UNIQUE (partner_id, transaction_type, direction)
);
CREATE INDEX IF NOT EXISTS partner_transaction_partner_idx
  ON partner_transaction (partner_id);

-- Per-partner, per-transaction, per-SNIP-type severity (effective dated) ----
-- severity: enforce-reject | warn | off  (Workstream 7 reads this)
CREATE TABLE IF NOT EXISTS partner_snip_policy (
  id                BIGSERIAL PRIMARY KEY,
  partner_id        BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
  transaction_type  TEXT NOT NULL DEFAULT '*', -- '*' = all transaction types
  snip_type         INTEGER NOT NULL CHECK (snip_type BETWEEN 1 AND 7),
  severity          TEXT NOT NULL
                    CHECK (severity IN ('enforce-reject','warn','off')),
  policy_name       TEXT NOT NULL DEFAULT 'edig-parity-v1',
  effective_from    DATE NOT NULL DEFAULT CURRENT_DATE,
  effective_to      DATE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS partner_snip_policy_lookup_idx
  ON partner_snip_policy (partner_id, transaction_type, snip_type, effective_from);

-- Acknowledgement profile: 999_only (default) | 999_plus_277CA -------------
CREATE TABLE IF NOT EXISTS partner_ack_profile (
  id              BIGSERIAL PRIMARY KEY,
  partner_id      BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
  ack_profile     TEXT NOT NULL DEFAULT '999_only'
                  CHECK (ack_profile IN ('999_only','999_plus_277CA')),
  effective_from  DATE NOT NULL DEFAULT CURRENT_DATE,
  effective_to    DATE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS partner_ack_profile_lookup_idx
  ON partner_ack_profile (partner_id, effective_from);

-- Contacts / enrollment ----------------------------------------------------
CREATE TABLE IF NOT EXISTS partner_contact (
  id            BIGSERIAL PRIMARY KEY,
  partner_id    BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
  contact_type  TEXT NOT NULL DEFAULT 'technical'
                CHECK (contact_type IN ('technical','billing','administrative','enrollment')),
  name          TEXT,
  email         TEXT,
  phone         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS partner_contact_partner_idx
  ON partner_contact (partner_id);

-- Trading Partner Agreement (TPA) metadata / versioning --------------------
CREATE TABLE IF NOT EXISTS partner_agreement (
  id                 BIGSERIAL PRIMARY KEY,
  partner_id         BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
  agreement_version  TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','active','terminated')),
  signed_date        DATE,
  effective_from     DATE,
  effective_to       DATE,
  document_ref       TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS partner_agreement_partner_idx
  ON partner_agreement (partner_id);
