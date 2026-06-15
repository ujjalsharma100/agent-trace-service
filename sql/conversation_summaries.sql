-- =========================================================================
-- Conversation summaries table
--
-- Stores LLM-generated session summaries keyed by ``conversation_id``
-- (sha256 over the original local transcript URL). One row per
-- (conversation_id, created_at) — re-running summary generation appends
-- a new row rather than replacing the prior summary, mirroring the
-- append-only ``session-summaries.jsonl`` on the client.
--
-- Scoped by (org_id, project_id, conversation_id, created_at).
-- =========================================================================

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,

    conversation_id TEXT NOT NULL,
    summary         TEXT NOT NULL,
    session_id      TEXT,

    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, project_id, conversation_id, created_at),
    FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS conv_summaries_org_project_idx
    ON conversation_summaries (org_id, project_id);
CREATE INDEX IF NOT EXISTS conv_summaries_conversation_id_idx
    ON conversation_summaries (org_id, project_id, conversation_id);
CREATE INDEX IF NOT EXISTS conv_summaries_updated_at_idx
    ON conversation_summaries (org_id, project_id, updated_at);
