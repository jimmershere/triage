-- Workstream 4 — Operational Helpdesk: case/ticket model linked to
-- claimtrace_claim and the specific claimtrace_event (999/277CA/rejection-report)
-- that triggered the case. Cases REFERENCE claim data, never edit it; they store
-- reference identifiers and the error location, not clinical content (PHI-safe).
--
-- Mirrors api/helpdesk_service.ensure_helpdesk_tables(). Additive only — numbered
-- 006 to avoid colliding with other branches that may use 002-004.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SEQUENCE IF NOT EXISTS helpdesk_case_number_seq;

CREATE TABLE IF NOT EXISTS helpdesk_case (
  case_id UUID PRIMARY KEY,
  case_number TEXT UNIQUE NOT NULL,
  tracking_id UUID REFERENCES claimtrace_claim(tracking_id) ON DELETE SET NULL,
  claim_id TEXT,
  source_event_id UUID REFERENCES claimtrace_event(event_id) ON DELETE SET NULL,
  trading_partner_id TEXT,
  submitter_id TEXT,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'submitter-notified', 'awaiting-resubmission', 'resolved')),
  priority TEXT NOT NULL DEFAULT 'normal',
  ack_type TEXT,                 -- 999 | 277CA | rejection-report
  reason_code TEXT,
  reason_text TEXT,
  segment_id TEXT,               -- offending segment (location only, no PHI)
  element_position TEXT,
  loop_id TEXT,
  resubmission_claim_id TEXT,    -- corrected resubmission, linked on resolve
  assigned_to TEXT,
  correlation_ids JSONB NOT NULL DEFAULT '{}',
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS helpdesk_case_status_idx ON helpdesk_case (status);
CREATE INDEX IF NOT EXISTS helpdesk_case_claim_idx ON helpdesk_case (claim_id);
CREATE INDEX IF NOT EXISTS helpdesk_case_partner_idx ON helpdesk_case (trading_partner_id);
CREATE INDEX IF NOT EXISTS helpdesk_case_tracking_idx ON helpdesk_case (tracking_id);

-- Append-only audit of case actions (PHI-safe: reference ids/locations/hashes).
CREATE TABLE IF NOT EXISTS helpdesk_case_event (
  event_id UUID PRIMARY KEY,
  case_id UUID NOT NULL REFERENCES helpdesk_case(case_id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  detail JSONB NOT NULL DEFAULT '{}',
  correlation_ids JSONB NOT NULL DEFAULT '{}',
  actor TEXT,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS helpdesk_case_event_case_ts_idx ON helpdesk_case_event (case_id, ts);

CREATE OR REPLACE FUNCTION reject_helpdesk_case_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'helpdesk_case_event is append-only; UPDATE and DELETE are forbidden';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS helpdesk_case_event_append_only_update ON helpdesk_case_event;
CREATE TRIGGER helpdesk_case_event_append_only_update
BEFORE UPDATE ON helpdesk_case_event
FOR EACH ROW EXECUTE FUNCTION reject_helpdesk_case_event_mutation();

DROP TRIGGER IF EXISTS helpdesk_case_event_append_only_delete ON helpdesk_case_event;
CREATE TRIGGER helpdesk_case_event_append_only_delete
BEFORE DELETE ON helpdesk_case_event
FOR EACH ROW EXECUTE FUNCTION reject_helpdesk_case_event_mutation();
