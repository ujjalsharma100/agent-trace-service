-- =========================================================================
-- Projects table
--
-- ``project_id`` is the CLI's path-based stable id (eg. "-Users-jane-foo");
-- it is unique within an org but may collide across orgs, so the natural
-- key is (org_id, project_id).
-- =========================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL,
    name            TEXT,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, project_id)
);

CREATE INDEX IF NOT EXISTS projects_org_project_idx
    ON projects (org_id, project_id);
