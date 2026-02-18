-- commit_links: maps git commits to the AI traces that contributed to them.
-- The post-commit hook creates one row per commit, linking it to all traces
-- that were active at the parent revision and touched the committed files.

CREATE TABLE IF NOT EXISTS commit_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL,

    commit_sha      TEXT NOT NULL,
    parent_sha      TEXT,
    trace_ids       JSONB NOT NULL,          -- ["trace-uuid-1", "trace-uuid-2", ...]
    files_changed   JSONB,                   -- ["src/foo.ts", "src/bar.py", ...]

    committed_at    TIMESTAMPTZ,             -- git commit author date
    ledger          JSONB,                   -- attribution ledger (per-line attribution)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (project_id, commit_sha)
);

CREATE INDEX IF NOT EXISTS commit_links_project_id_idx
    ON commit_links (project_id);
CREATE INDEX IF NOT EXISTS commit_links_commit_sha_idx
    ON commit_links (project_id, commit_sha);
CREATE INDEX IF NOT EXISTS commit_links_parent_sha_idx
    ON commit_links (project_id, parent_sha);
