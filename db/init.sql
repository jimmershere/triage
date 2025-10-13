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
