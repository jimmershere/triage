-- Minimal schema for TurboEDI Starter

CREATE TABLE IF NOT EXISTS imports (
  id SERIAL PRIMARY KEY,
  job_id UUID NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  file_type TEXT NOT NULL DEFAULT 'unknown',
  byte_size INTEGER NOT NULL,
  uploaded_by TEXT,
  trading_partner_id TEXT,
  original_content BYTEA,
  status TEXT NOT NULL DEFAULT 'queued',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  processed_at TIMESTAMP,
  claims_count INTEGER,
  order_lines_count INTEGER
);

-- Backfill columns for environments that created `imports` before these fields existed.
ALTER TABLE imports
  ADD COLUMN IF NOT EXISTS original_content BYTEA,
  ADD COLUMN IF NOT EXISTS status TEXT,
  ADD COLUMN IF NOT EXISTS file_type TEXT,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS claims_count INTEGER,
  ADD COLUMN IF NOT EXISTS order_lines_count INTEGER;

ALTER TABLE imports
  ALTER COLUMN file_type SET DEFAULT 'unknown';
UPDATE imports SET file_type = 'unknown' WHERE file_type IS NULL;

UPDATE imports SET status = 'queued' WHERE status IS NULL;
ALTER TABLE imports
  ALTER COLUMN status SET DEFAULT 'queued';
ALTER TABLE imports
  ALTER COLUMN status SET NOT NULL;

UPDATE imports SET created_at = NOW() WHERE created_at IS NULL;
ALTER TABLE imports
  ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE imports
  ALTER COLUMN created_at SET NOT NULL;

CREATE TABLE IF NOT EXISTS claims (
  id SERIAL PRIMARY KEY,
  import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
  claim_id TEXT,
  amount NUMERIC(12,2),
  raw_claim TEXT
);

CREATE TABLE IF NOT EXISTS order_lines (
  id SERIAL PRIMARY KEY,
  import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
  line_no INTEGER,
  item_id TEXT,
  qty NUMERIC(12,3),
  price NUMERIC(12,2),
  raw_line TEXT
);

CREATE TABLE IF NOT EXISTS acks (
  id SERIAL PRIMARY KEY,
  import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
  ack_type TEXT NOT NULL,      -- e.g., '999-like' or 'CONTRL-like'
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_users (
  username TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('view','update','create','admin')),
  allow_portal BOOLEAN NOT NULL DEFAULT TRUE,
  allow_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS app_users_role_idx ON app_users (role);

ALTER TABLE app_users
  ADD COLUMN IF NOT EXISTS allow_portal BOOLEAN;
ALTER TABLE app_users
  ADD COLUMN IF NOT EXISTS allow_admin BOOLEAN;
ALTER TABLE app_users
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE app_users
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

UPDATE app_users SET allow_portal = TRUE WHERE allow_portal IS NULL;
UPDATE app_users SET allow_admin = FALSE WHERE allow_admin IS NULL;
UPDATE app_users SET created_at = NOW() WHERE created_at IS NULL;
UPDATE app_users SET updated_at = NOW() WHERE updated_at IS NULL;

ALTER TABLE app_users ALTER COLUMN allow_portal SET DEFAULT TRUE;
ALTER TABLE app_users ALTER COLUMN allow_portal SET NOT NULL;
ALTER TABLE app_users ALTER COLUMN allow_admin SET DEFAULT FALSE;
ALTER TABLE app_users ALTER COLUMN allow_admin SET NOT NULL;
ALTER TABLE app_users ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE app_users ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE app_users ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE app_users ALTER COLUMN updated_at SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_constraint
     WHERE conname = 'app_users_role_check'
  ) THEN
    ALTER TABLE app_users
      ADD CONSTRAINT app_users_role_check CHECK (role IN ('view','update','create','admin'));
  END IF;
END$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS era_835_header (
  st_control TEXT PRIMARY KEY,
  bpr_method TEXT,
  bpr_amount NUMERIC,
  trn_trace TEXT,
  payer_name TEXT,
  payer_id TEXT,
  payee_name TEXT,
  payee_id TEXT,
  chk_date DATE
);

CREATE TABLE IF NOT EXISTS era_835_clp (
  st_control TEXT,
  claim_id TEXT,
  status TEXT,
  total_charge NUMERIC,
  paid NUMERIC,
  patient_resp NUMERIC,
  payer_ctrl TEXT,
  facility TEXT,
  claim_freq TEXT
);

CREATE TABLE IF NOT EXISTS era_835_cas (
  st_control TEXT,
  claim_id TEXT,
  adj_group TEXT,
  adj_reason TEXT,
  amount NUMERIC,
  quantity INT
);

CREATE TABLE IF NOT EXISTS era_835_plb (
  st_control TEXT,
  provider_id TEXT,
  fiscal_date DATE,
  adj_qual TEXT,
  ref_id TEXT,
  amount NUMERIC
);

CREATE TABLE IF NOT EXISTS eligibility_271 (
  st_control TEXT,
  subscriber_id TEXT,
  payer_id TEXT,
  eb_code TEXT,
  service_type TEXT,
  coverage_plan TEXT,
  network TEXT,
  description TEXT
);

CREATE TABLE IF NOT EXISTS claim_status_277 (
  st_control TEXT,
  subscriber_id TEXT,
  payer_claim_ctrl TEXT,
  status_info TEXT,
  status_date DATE,
  amount NUMERIC,
  quantity INT
);
