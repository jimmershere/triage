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

-- Optional: app-side mapping if not using OIDC/LDAP exclusively
CREATE TABLE IF NOT EXISTS app_roles (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    roles TEXT[] NOT NULL DEFAULT '{}',
    perms TEXT[] NOT NULL DEFAULT '{}',
    UNIQUE (username)
);
