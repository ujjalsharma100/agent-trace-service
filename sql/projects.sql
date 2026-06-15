-- =========================================================================
-- Projects table
--
-- ``project_id`` is a human-readable slug (eg. "myrepo"), unique within an
-- org. The remote URL the CLI binds to has the shape
-- ``<scheme>://<host>/<org_slug>/<project_id>``, mirroring GitHub's
-- ``org/repo`` mental model. The natural key is (org_id, project_id).
--
-- Slug shape mirrors the orgs.slug CHECK: starts with [a-z0-9], may contain
-- ``[a-z0-9._-]``, max 64 chars. Lowercased to keep URL semantics
-- case-insensitive and to avoid filename-collision surprises on macOS.
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

    UNIQUE (org_id, project_id),
    CONSTRAINT projects_slug_shape
        CHECK (project_id ~ '^[a-z0-9][a-z0-9._-]{0,63}$')
);

CREATE INDEX IF NOT EXISTS projects_org_project_idx
    ON projects (org_id, project_id);
