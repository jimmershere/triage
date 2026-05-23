-- db/triage_partner_profiles.sql
CREATE TABLE IF NOT EXISTS partner_profiles (
  partner_key text PRIMARY KEY,
  -- Payer / Provider identifiers used inside NM1 segments
  payer_id text,
  payer_id_qual text DEFAULT 'PI',      -- e.g., PI=Payor Identification, XV=HC Plan ID
  provider_id text,
  provider_id_qual text DEFAULT 'XX',   -- XX=NPI
  -- Envelope overrides (bots will still use partner settings unless you set these here)
  isa_sender_id text,
  isa_receiver_id text,
  gs_sender_code text,
  gs_receiver_code text,
  usage_indicator text CHECK (usage_indicator IN ('T','P')),
  -- Optional metadata
  description text
);

-- Seed a 'default' profile you can edit
INSERT INTO partner_profiles (partner_key, payer_id, provider_id, usage_indicator, description)
VALUES ('default', 'PAYERID123', '1234567893', 'T', 'Edit me with real payer/provider IDs')
ON CONFLICT (partner_key) DO NOTHING;
