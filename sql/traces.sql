-- =========================================================================
-- Traces table — opaque trace JSON with indexed key fields.
-- Scoped by (org_id, project_id, trace_id).
-- =========================================================================

CREATE TABLE IF NOT EXISTS traces (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id        TEXT NOT NULL,
    user_id           TEXT NOT NULL,

    -- Key fields broken out from the trace record for querying
    trace_id          TEXT NOT NULL,
    version           TEXT NOT NULL DEFAULT '1.0',
    trace_timestamp   TIMESTAMPTZ NOT NULL,
    vcs               JSONB,
    tool              JSONB,
    files             JSONB,
    metadata          JSONB,

    -- The full trace record stored as-is
    trace_record      JSONB NOT NULL,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, project_id, trace_id),
    FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS traces_org_project_idx       ON traces (org_id, project_id);
CREATE INDEX IF NOT EXISTS traces_user_id_idx           ON traces (user_id);
CREATE INDEX IF NOT EXISTS traces_trace_timestamp_idx   ON traces (org_id, project_id, trace_timestamp);
CREATE INDEX IF NOT EXISTS traces_tool_idx              ON traces USING GIN (tool);
CREATE INDEX IF NOT EXISTS traces_metadata_idx          ON traces USING GIN (metadata);

-- For blame queries: find traces by VCS revision
CREATE INDEX IF NOT EXISTS traces_vcs_revision_idx
    ON traces ((vcs->>'revision'));

-- For blame queries: find traces by file path (GIN on files JSONB)
CREATE INDEX IF NOT EXISTS traces_files_idx
    ON traces USING GIN (files);
