-- Minimal schema for TurboEDI Starter

CREATE TABLE IF NOT EXISTS imports (
  id SERIAL PRIMARY KEY,
  filename TEXT NOT NULL,
  file_type TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  processed_at TIMESTAMP NOT NULL DEFAULT NOW(),
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
