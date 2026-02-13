-- =========================================================================
-- Traces table
-- =========================================================================

CREATE TABLE IF NOT EXISTS traces (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
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

    UNIQUE (project_id, trace_id)
);

CREATE INDEX IF NOT EXISTS traces_project_id_idx       ON traces (project_id);
CREATE INDEX IF NOT EXISTS traces_user_id_idx          ON traces (user_id);
CREATE INDEX IF NOT EXISTS traces_trace_timestamp_idx  ON traces (trace_timestamp);
CREATE INDEX IF NOT EXISTS traces_tool_idx             ON traces USING GIN (tool);
CREATE INDEX IF NOT EXISTS traces_metadata_idx         ON traces USING GIN (metadata);
