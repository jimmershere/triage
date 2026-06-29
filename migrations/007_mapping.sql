-- Workstream 1: advisory mapping-suggestion service (human-approval queue).
-- Mirrors api/mapping_routes.ensure_mapping_tables() so optional SQL bootstrap
-- creates the same tables the application's startup migration ensures.
--
-- Governance: these tables back an ADVISORY queue. The advisor proposes
-- candidate X12<->flat-file mappings and 999-derived validation rules; a human
-- approves/rejects each one. Approval records a *versioned* rule (config) — it
-- never applies a mapping to a submission or mutates claim data.
--
-- High migration number (007) chosen to avoid colliding with sibling branches
-- that may use 002-006.

CREATE TABLE IF NOT EXISTS mapping_suggestion (
  id BIGSERIAL PRIMARY KEY,
  transaction_set TEXT,
  partner_id TEXT,
  rule_type TEXT NOT NULL,                 -- 'element_map' | 'validation_rule'
  rule_key TEXT NOT NULL,                  -- stable, value-independent identity
  summary TEXT,
  score DOUBLE PRECISION NOT NULL DEFAULT 0,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  payload JSONB NOT NULL,                  -- PHI-safe structural suggestion
  status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected'
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  decided_at TIMESTAMPTZ,
  decided_by TEXT,
  decision_reason TEXT
);

CREATE INDEX IF NOT EXISTS mapping_suggestion_status_idx ON mapping_suggestion (status);
CREATE INDEX IF NOT EXISTS mapping_suggestion_rule_key_idx ON mapping_suggestion (rule_key);

-- Approved, versioned rules. A new approval for the same rule_key supersedes
-- the prior active version (active = FALSE on the old row).
CREATE TABLE IF NOT EXISTS mapping_rule (
  id BIGSERIAL PRIMARY KEY,
  suggestion_id BIGINT REFERENCES mapping_suggestion(id) ON DELETE SET NULL,
  transaction_set TEXT,
  partner_id TEXT,
  rule_type TEXT NOT NULL,
  rule_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  definition JSONB NOT NULL,
  approved_by TEXT NOT NULL,
  approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (rule_key, version)
);

CREATE INDEX IF NOT EXISTS mapping_rule_active_idx ON mapping_rule (rule_key, active);
