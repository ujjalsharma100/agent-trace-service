-- =========================================================================
-- Blobs table — content-addressed blob store for conversation transcripts.
--
-- The CLI uploads a blob with ``POST /api/v1/blobs`` (raw body).  The
-- service computes SHA-256, stores the bytes, and returns the hash.
-- Conversation pointers in ``conversation_contents`` then reference this
-- hash. Blobs are deduplicated globally — the same transcript pushed by
-- two different orgs is stored once. Access control happens at the
-- pointer level (conversation_contents is org-scoped).
--
-- Storage backend: Postgres ``bytea`` for M0. S3/MinIO is M1 (Phase B).
-- =========================================================================

CREATE TABLE IF NOT EXISTS blobs (
    sha256        TEXT PRIMARY KEY,         -- hex of SHA-256(content), 64 chars
    size          BIGINT NOT NULL,
    content_type  TEXT,                     -- optional MIME hint
    storage_url   TEXT,                     -- non-NULL when stored externally (S3); NULL for inline
    bytes         BYTEA,                    -- inline storage (M0 default)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
