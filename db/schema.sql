CREATE TABLE IF NOT EXISTS announcements (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',  -- info|warning|critical
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faqs (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT NOT NULL
);

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
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    processed_at TIMESTAMP,
    claims_count INTEGER,
    order_lines_count INTEGER
);

CREATE TABLE IF NOT EXISTS claims (
    id SERIAL PRIMARY KEY,
    import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
    claim_id TEXT,
    amount NUMERIC(12, 2),
    raw_claim TEXT
);

CREATE TABLE IF NOT EXISTS order_lines (
    id SERIAL PRIMARY KEY,
    import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
    line_no INTEGER,
    item_id TEXT,
    qty NUMERIC(12, 3),
    price NUMERIC(12, 2),
    raw_line TEXT
);

CREATE TABLE IF NOT EXISTS acks (
    id SERIAL PRIMARY KEY,
    import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE,
    ack_type TEXT NOT NULL,      -- e.g., '999-like' or 'CONTRL-like'
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    content TEXT NOT NULL
);

-- Optional: app-side mapping if not using OIDC/LDAP exclusively
CREATE TABLE IF NOT EXISTS app_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('view', 'submit', 'administrator')),
    allow_portal BOOLEAN NOT NULL DEFAULT TRUE,
    allow_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_users_role_idx ON app_users (role);

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
