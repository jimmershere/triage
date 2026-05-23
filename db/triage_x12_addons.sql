-- db/triage_x12_addons.sql
-- Tables for 835 ERA, 271 Eligibility, 277 Claim Status

CREATE TABLE IF NOT EXISTS era_835_header (
  st_control text PRIMARY KEY,
  bpr_method text, bpr_amount numeric, trn_trace text,
  payer_name text, payer_id text, payee_name text, payee_id text,
  chk_date date
);

CREATE TABLE IF NOT EXISTS era_835_clp (
  st_control text, claim_id text, status text,
  total_charge numeric, paid numeric, patient_resp numeric,
  payer_ctrl text, facility text, claim_freq text
);

CREATE TABLE IF NOT EXISTS era_835_cas (
  st_control text, claim_id text, adj_group text, adj_reason text, amount numeric, quantity int
);

CREATE TABLE IF NOT EXISTS era_835_plb (
  st_control text, provider_id text, fiscal_date date, adj_qual text, ref_id text, amount numeric
);

CREATE TABLE IF NOT EXISTS eligibility_271 (
  st_control text, subscriber_id text, payer_id text,
  eb_code text, service_type text, coverage_plan text, network text, description text
);

CREATE TABLE IF NOT EXISTS claim_status_277 (
  st_control text, subscriber_id text, payer_claim_ctrl text,
  status_info text, status_date date, amount numeric, quantity int
);
