CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS claim_event (
  event_id UUID NOT NULL,
  claim_id TEXT NOT NULL,
  bundle_id TEXT NULL,
  prior_state_hash TEXT NULL,
  new_state_hash TEXT NOT NULL,
  payload_location TEXT NOT NULL,
  operation_type TEXT NOT NULL CHECK (operation_type IN
     ('INGEST','VALIDATE','SPLIT','BUNDLE','ROUTE','ACK','ADJUDICATE','TRANSFORM','MERKLE_ROOT')),
  service_name TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  correlation_ids JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS claim_event_default PARTITION OF claim_event DEFAULT;

DO $$
DECLARE
  start_month date := date_trunc('month', now())::date;
  end_month date := (date_trunc('month', now()) + interval '1 month')::date;
  partition_name text := 'claim_event_' || to_char(start_month, 'YYYY_MM');
BEGIN
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS %I PARTITION OF claim_event FOR VALUES FROM (%L) TO (%L)',
    partition_name,
    start_month,
    end_month
  );
END$$;

CREATE INDEX IF NOT EXISTS claim_event_claim_ts_idx ON claim_event (claim_id, ts);
CREATE INDEX IF NOT EXISTS claim_event_bundle_ts_idx ON claim_event (bundle_id, ts);
CREATE INDEX IF NOT EXISTS claim_event_correlation_gin_idx ON claim_event USING GIN (correlation_ids);

CREATE OR REPLACE FUNCTION reject_claim_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'claim_event is append-only; UPDATE and DELETE are forbidden';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS claim_event_append_only_update ON claim_event;
CREATE TRIGGER claim_event_append_only_update
BEFORE UPDATE ON claim_event
FOR EACH ROW EXECUTE FUNCTION reject_claim_event_mutation();

DROP TRIGGER IF EXISTS claim_event_append_only_delete ON claim_event;
CREATE TRIGGER claim_event_append_only_delete
BEFORE DELETE ON claim_event
FOR EACH ROW EXECUTE FUNCTION reject_claim_event_mutation();

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'claim_app') THEN
    REVOKE UPDATE, DELETE ON claim_event FROM claim_app;
  END IF;
END$$;
