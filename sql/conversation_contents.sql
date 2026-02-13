-- =========================================================================
-- Conversation contents table
--
-- Stores the actual content for conversation URLs found in trace records.
-- The URL itself is the unique identifier — trace records reference URLs,
-- and this table provides the content lookup.
-- =========================================================================

CREATE TABLE IF NOT EXISTS conversation_contents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL,
    url             TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (project_id, url)
);

CREATE INDEX IF NOT EXISTS conv_contents_project_id_idx ON conversation_contents (project_id);
CREATE INDEX IF NOT EXISTS conv_contents_url_idx        ON conversation_contents (url);
