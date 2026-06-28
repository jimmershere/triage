-- Workstream 5 — 835 Restore: immutable storage of every produced/received 835
-- and its parsed structure, linked to claimtrace_claim / claimtrace_event.
--
-- Mirrors api/era_service.ensure_era_tables() so an optional SQL bootstrap
-- creates the same tables the application creates at startup. Additive only —
-- does not touch existing tables (numbered 005 to avoid colliding with other
-- branches that may use 002-004).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS era_artifact (
  artifact_id UUID PRIMARY KEY,
  tracking_id UUID REFERENCES claimtrace_claim(tracking_id) ON DELETE SET NULL,
  claim_id TEXT,
  trn TEXT,
  st_control TEXT,
  direction TEXT NOT NULL DEFAULT 'produced'
    CHECK (direction IN ('produced', 'received')),
  origin TEXT NOT NULL DEFAULT 'original'
    CHECK (origin IN ('original', 'reconstructed', 'reversal', 'replacement')),
  content_sha256 TEXT NOT NULL,
  raw_835 BYTEA NOT NULL,
  parsed JSONB NOT NULL DEFAULT '{}',
  bpr_amount NUMERIC,
  bpr_method TEXT,
  payer_id TEXT,
  payee_id TEXT,
  balanced BOOLEAN NOT NULL DEFAULT TRUE,
  balance_report JSONB NOT NULL DEFAULT '{}',
  source_artifact_id UUID REFERENCES era_artifact(artifact_id) ON DELETE SET NULL,
  reverses_artifact_id UUID REFERENCES era_artifact(artifact_id) ON DELETE SET NULL,
  correlation_ids JSONB NOT NULL DEFAULT '{}',
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS era_artifact_trn_idx ON era_artifact (trn);
CREATE INDEX IF NOT EXISTS era_artifact_claim_idx ON era_artifact (claim_id);
CREATE INDEX IF NOT EXISTS era_artifact_tracking_idx ON era_artifact (tracking_id);
CREATE INDEX IF NOT EXISTS era_artifact_hash_idx ON era_artifact (content_sha256);
CREATE INDEX IF NOT EXISTS era_artifact_origin_idx ON era_artifact (origin);

-- Stored 835 artifacts are immutable; re-delivery must be byte-for-byte and the
-- audit trail must be tamper-evident. Forbid UPDATE and DELETE.
CREATE OR REPLACE FUNCTION reject_era_artifact_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'era_artifact is immutable; UPDATE and DELETE are forbidden';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS era_artifact_immutable_update ON era_artifact;
CREATE TRIGGER era_artifact_immutable_update
BEFORE UPDATE ON era_artifact
FOR EACH ROW EXECUTE FUNCTION reject_era_artifact_mutation();

DROP TRIGGER IF EXISTS era_artifact_immutable_delete ON era_artifact;
CREATE TRIGGER era_artifact_immutable_delete
BEFORE DELETE ON era_artifact
FOR EACH ROW EXECUTE FUNCTION reject_era_artifact_mutation();
