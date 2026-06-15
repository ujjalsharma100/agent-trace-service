-- =========================================================================
-- Conversation contents table
--
-- Stores transcript pointers / inline content keyed by ``conversation_id``
-- (sha256 over the original local transcript URL). Two storage modes
-- coexist:
--
--   * Inline ``content`` (or ``content_b64`` for binary) for blobs below
--     the chunk threshold. Kept here for round-trip simplicity.
--   * Pointer to ``blobs.sha256`` for chunked uploads. The CLI HEAD/POSTs
--     to ``/api/v1/blobs`` first; this row only carries the hash.
--
-- Scoped by (org_id, project_id, conversation_id).
-- =========================================================================

CREATE TABLE IF NOT EXISTS conversation_contents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,

    conversation_id TEXT NOT NULL,

    -- Inline storage (small blobs, < CHUNK_THRESHOLD)
    content         TEXT,
    content_b64     TEXT,

    -- Chunked storage (pointer to blobs table)
    content_sha256  TEXT REFERENCES blobs(sha256) ON DELETE SET NULL,
    size            BIGINT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, project_id, conversation_id),
    FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS conv_contents_org_project_idx
    ON conversation_contents (org_id, project_id);
CREATE INDEX IF NOT EXISTS conv_contents_sha_idx
    ON conversation_contents (content_sha256);
