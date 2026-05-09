-- =========================================================================
-- Tokens table — opaque bearer tokens scoped to (org, project|null).
--
-- A token's plaintext form is shown exactly once at issue time. Only the
-- SHA-256 hash is persisted; lookups happen by hash.  ``project_id`` NULL
-- means the token is org-scoped (full access across that org's projects).
-- =========================================================================

CREATE TABLE IF NOT EXISTS tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id    TEXT,                              -- NULL = org-scoped
    scopes        TEXT[] NOT NULL DEFAULT ARRAY['read','write'],
    token_hash    TEXT NOT NULL UNIQUE,              -- SHA-256 of the issued opaque token
    prefix        TEXT NOT NULL,                     -- first 8 chars of the token, for display only
    name          TEXT,                              -- optional human label
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tokens_org_idx
    ON tokens (org_id);

-- Fast lookup of a presented token: indexed by hash, with revoked tokens
-- effectively excluded by callers.
CREATE INDEX IF NOT EXISTS tokens_active_lookup_idx
    ON tokens (token_hash)
    WHERE revoked_at IS NULL;
