-- =========================================================================
-- Orgs table — top-level tenant. Every other domain row carries org_id.
-- =========================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS orgs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,
    name        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default org used by self-hosted single-tenant deployments. The fixed UUID
-- lets the CLI / docs reference it without a lookup.
INSERT INTO orgs (id, slug, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'default', 'Default org')
ON CONFLICT (id) DO NOTHING;
