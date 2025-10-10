
-- PostgreSQL schema for portal content & RBAC hints
CREATE SCHEMA IF NOT EXISTS portal;

CREATE TABLE IF NOT EXISTS portal.announcements (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  published_at TIMESTAMPTZ DEFAULT now(),
  is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS portal.faqs (
  id SERIAL PRIMARY KEY,
  q TEXT NOT NULL,
  a TEXT NOT NULL,
  sort_order INT DEFAULT 100
);

-- Example RBAC mapping (if using app-level authorization in addition to Keycloak/LDAP)
CREATE TABLE IF NOT EXISTS portal.user_roles (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('Submitter','Reviewer','Admin')),
  UNIQUE(user_id, role)
);
