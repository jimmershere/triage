-- Claimtrace persistent schema used by api/claimtrace_service.py.
-- This migration mirrors the application's startup migration so optional SQL
-- bootstrap creates the same DB-backed dashboard tables the app expects.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS claimtrace_claim (
  tracking_id UUID PRIMARY KEY,
  claim_id TEXT NOT NULL,
  claim_hash_id TEXT NOT NULL,
  import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
  job_id UUID UNIQUE,
  filename TEXT,
  uploaded_by TEXT,
  trading_partner_id TEXT,
  submitter_id TEXT,
  trace_id TEXT,
  state_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'tracked',
  action_state TEXT NOT NULL DEFAULT 'none',
  recommended_next_steps JSONB NOT NULL DEFAULT '[]',
  raw_excerpt TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS claimtrace_claim_claim_id_idx ON claimtrace_claim (claim_id);
CREATE INDEX IF NOT EXISTS claimtrace_claim_hash_idx ON claimtrace_claim (claim_hash_id);
CREATE INDEX IF NOT EXISTS claimtrace_claim_partner_idx ON claimtrace_claim (trading_partner_id);
CREATE INDEX IF NOT EXISTS claimtrace_claim_submitter_idx ON claimtrace_claim (submitter_id);
CREATE INDEX IF NOT EXISTS claimtrace_claim_action_idx ON claimtrace_claim (action_state);

CREATE TABLE IF NOT EXISTS claimtrace_event (
  event_id UUID PRIMARY KEY,
  tracking_id UUID REFERENCES claimtrace_claim(tracking_id) ON DELETE SET NULL,
  claim_id TEXT NOT NULL,
  bundle_id TEXT,
  operation_type TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  payload_location TEXT NOT NULL,
  service_name TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  correlation_ids JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS claimtrace_event_claim_ts_idx ON claimtrace_event (claim_id, ts);
CREATE INDEX IF NOT EXISTS claimtrace_event_tracking_ts_idx ON claimtrace_event (tracking_id, ts);
CREATE INDEX IF NOT EXISTS claimtrace_event_corr_gin_idx ON claimtrace_event USING GIN (correlation_ids);

CREATE OR REPLACE FUNCTION reject_claimtrace_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'claimtrace_event is append-only; UPDATE and DELETE are forbidden';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS claimtrace_event_append_only_update ON claimtrace_event;
CREATE TRIGGER claimtrace_event_append_only_update
BEFORE UPDATE ON claimtrace_event
FOR EACH ROW EXECUTE FUNCTION reject_claimtrace_event_mutation();

DROP TRIGGER IF EXISTS claimtrace_event_append_only_delete ON claimtrace_event;
CREATE TRIGGER claimtrace_event_append_only_delete
BEFORE DELETE ON claimtrace_event
FOR EACH ROW EXECUTE FUNCTION reject_claimtrace_event_mutation();
