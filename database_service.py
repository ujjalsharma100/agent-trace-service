"""
Database access layer for agent-trace-service.

All raw SQL lives here — no other module should import psycopg2 directly
(except init_db.py for schema management).
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg2
import psycopg2.extras
from flask import g

from model import (
    CommitLink,
    Project,
    ProjectStats,
    TraceFields,
)

# Register UUID adapter once at import time
psycopg2.extras.register_uuid()


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _build_database_url() -> str:
    """Build a PostgreSQL connection URL from individual env vars."""
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    user = os.environ.get("DB_USER", "postgres")
    password = os.environ.get("DB_PASSWORD", "postgres")
    name = os.environ.get("DB_NAME", "agent_trace")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


# ---------------------------------------------------------------------------
# Connection management (Flask per-request pattern)
# ---------------------------------------------------------------------------

def get_db():
    """Return the per-request database connection, creating one if needed."""
    if "db" not in g:
        g.db = psycopg2.connect(_build_database_url())
        g.db.autocommit = False
    return g.db


def close_db(exc):
    """Tear down the per-request connection (called by Flask)."""
    db = g.pop("db", None)
    if db is not None:
        if exc:
            db.rollback()
        else:
            db.commit()
        db.close()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def check_db_health() -> bool:
    """Return True if the database is reachable."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT 1")
    return True


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def ensure_project(project_id: str) -> None:
    """Insert a project row if it doesn't already exist."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (project_id) VALUES (%s) ON CONFLICT (project_id) DO NOTHING",
            (project_id,),
        )


def get_project(project_id: str) -> Project | None:
    """Fetch a single project by its project_id."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM projects WHERE project_id = %s", (project_id,))
        row = cur.fetchone()
    if not row:
        return None
    return Project(
        id=str(row["id"]),
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_project_stats(project_id: str) -> ProjectStats:
    """Return aggregate stats for a project."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT COUNT(*) AS count FROM traces WHERE project_id = %s",
            (project_id,),
        )
        trace_count = cur.fetchone()["count"]

        cur.execute(
            "SELECT trace_timestamp FROM traces WHERE project_id = %s ORDER BY trace_timestamp DESC LIMIT 1",
            (project_id,),
        )
        latest = cur.fetchone()

        cur.execute(
            "SELECT COUNT(DISTINCT user_id) AS count FROM traces WHERE project_id = %s",
            (project_id,),
        )
        unique_users = cur.fetchone()["count"]

        cur.execute(
            "SELECT COUNT(*) AS count FROM conversation_contents WHERE project_id = %s",
            (project_id,),
        )
        conv_count = cur.fetchone()["count"]

    return ProjectStats(
        trace_count=trace_count,
        conversation_count=conv_count,
        unique_users=unique_users,
        latest_trace_at=latest["trace_timestamp"].isoformat() if latest else None,
    )


def upsert_project(project_id: str, name: str | None = None, description: str | None = None) -> Project:
    """Create or update a project, returning the resulting row."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO projects (project_id, name, description)
            VALUES (%s, %s, %s)
            ON CONFLICT (project_id) DO UPDATE SET
                name        = COALESCE(EXCLUDED.name, projects.name),
                description = COALESCE(EXCLUDED.description, projects.description),
                updated_at  = NOW()
            RETURNING *
            """,
            (project_id, name, description),
        )
        row = cur.fetchone()

    return Project(
        id=str(row["id"]),
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------

def insert_trace(project_id: str, user_id: str, fields: TraceFields) -> None:
    """Insert a single trace row (no-op on conflict)."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO traces (
                project_id, user_id,
                trace_id, version, trace_timestamp,
                vcs, tool, files, metadata,
                trace_record
            ) VALUES (
                %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s
            ) ON CONFLICT (project_id, trace_id) DO NOTHING
            """,
            (
                project_id, user_id,
                fields.trace_id, fields.version, fields.trace_timestamp,
                fields.vcs, fields.tool, fields.files, fields.metadata,
                fields.trace_record,
            ),
        )


def list_traces(
    project_id: str,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Any], int]:
    """
    Return a paginated list of trace records + total count.
    """
    filters = ["project_id = %s"]
    params: list[Any] = [project_id]

    if since:
        filters.append("trace_timestamp >= %s")
        params.append(since)
    if until:
        filters.append("trace_timestamp <= %s")
        params.append(until)

    where = " AND ".join(filters)

    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT trace_record FROM traces WHERE {where} ORDER BY trace_timestamp DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = cur.fetchall()

        cur.execute(f"SELECT COUNT(*) AS count FROM traces WHERE {where}", params)
        total = cur.fetchone()["count"]

    return [r["trace_record"] for r in rows], total


def upsert_conversation_contents(project_id: str, user_id: str, contents: list[dict[str, str]]) -> None:
    """
    Upsert conversation contents — url is the unique key per project.

    Each item in *contents* is {"url": ..., "content": ...}.
    If the url already exists for this project, update the content.
    """
    if not contents:
        return
    db = get_db()
    with db.cursor() as cur:
        for item in contents:
            cur.execute(
                """
                INSERT INTO conversation_contents (project_id, user_id, url, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (project_id, url) DO UPDATE SET
                    content    = EXCLUDED.content,
                    updated_at = NOW()
                """,
                (project_id, user_id, item["url"], item["content"]),
            )


def get_conversation_content(project_id: str, url: str) -> str | None:
    """Look up conversation content by URL. Returns content or None."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT content FROM conversation_contents WHERE project_id = %s AND url = %s LIMIT 1",
            (project_id, url),
        )
        row = cur.fetchone()
    return row["content"] if row else None


def get_trace(project_id: str, trace_id: str) -> dict[str, Any] | None:
    """
    Return a single trace record with ownership info, or None.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT trace_record, user_id FROM traces WHERE project_id = %s AND trace_id = %s LIMIT 1",
            (project_id, trace_id),
        )
        row = cur.fetchone()
        if not row:
            return None

    return {
        "trace": row["trace_record"],
        "user_id": row["user_id"],
    }


# ---------------------------------------------------------------------------
# Commit Links
# ---------------------------------------------------------------------------

def insert_commit_link(
    project_id: str,
    user_id: str,
    commit_sha: str,
    parent_sha: str | None,
    trace_ids: list[str],
    files_changed: list[str] | None,
    committed_at: str | None,
    ledger: dict[str, Any] | None = None,
) -> None:
    """Insert a commit-trace link (upsert on project_id + commit_sha)."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commit_links (
                project_id, user_id, commit_sha, parent_sha,
                trace_ids, files_changed, committed_at, ledger
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (project_id, commit_sha) DO UPDATE SET
                parent_sha    = EXCLUDED.parent_sha,
                trace_ids     = EXCLUDED.trace_ids,
                files_changed = EXCLUDED.files_changed,
                committed_at  = EXCLUDED.committed_at,
                ledger        = EXCLUDED.ledger,
                user_id       = EXCLUDED.user_id
            """,
            (
                project_id,
                user_id,
                commit_sha,
                parent_sha,
                json.dumps(trace_ids),
                json.dumps(files_changed) if files_changed else None,
                committed_at,
                json.dumps(ledger) if ledger else None,
            ),
        )


def get_commit_link(project_id: str, commit_sha: str) -> dict[str, Any] | None:
    """Look up a commit link by project + commit SHA."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, project_id, user_id, commit_sha, parent_sha,
                   trace_ids, files_changed, committed_at, ledger, created_at
            FROM commit_links
            WHERE project_id = %s AND commit_sha = %s
            LIMIT 1
            """,
            (project_id, commit_sha),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row["id"]),
        "project_id": row["project_id"],
        "user_id": row["user_id"],
        "commit_sha": row["commit_sha"],
        "parent_sha": row["parent_sha"],
        "trace_ids": row["trace_ids"],           # JSONB → Python list
        "files_changed": row["files_changed"],   # JSONB → Python list or None
        "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
        "ledger": row["ledger"],                 # JSONB → Python dict or None
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def get_ledger(project_id: str, commit_sha: str) -> dict[str, Any] | None:
    """Look up the attribution ledger for a commit. Returns the ledger dict or None."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT ledger
            FROM commit_links
            WHERE project_id = %s AND commit_sha = %s AND ledger IS NOT NULL
            LIMIT 1
            """,
            (project_id, commit_sha),
        )
        row = cur.fetchone()
    if not row:
        return None
    return row["ledger"]  # JSONB → Python dict


# ---------------------------------------------------------------------------
# Attribution queries (blame support)
# ---------------------------------------------------------------------------

def find_traces_by_ids(project_id: str, trace_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch specific traces by their IDs.  Returns the full trace_record for each."""
    if not trace_ids:
        return []
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT trace_id, trace_record, vcs, tool, files, trace_timestamp
            FROM traces
            WHERE project_id = %s AND trace_id = ANY(%s)
            """,
            (project_id, trace_ids),
        )
        return [dict(row) for row in cur.fetchall()]


def find_traces_by_revision(
    project_id: str,
    revision: str,
) -> list[dict[str, Any]]:
    """Find all traces matching a VCS revision (no file filter).

    Caller should filter by file in Python for lenient path matching
    (e.g. trace path "vite.config.js" vs blamed path "frontend/vite.config.js").
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT trace_id, trace_record, vcs, tool, files, trace_timestamp
            FROM traces
            WHERE project_id = %s AND vcs->>'revision' = %s
            ORDER BY trace_timestamp DESC
            """,
            (project_id, revision),
        )
        return [dict(row) for row in cur.fetchall()]


def find_traces_by_revision_and_file(
    project_id: str,
    revision: str,
    file_path: str,
) -> list[dict[str, Any]]:
    """Find traces matching a VCS revision that touch a specific file.

    Uses the JSONB vcs->>'revision' field and checks whether the files
    array contains an entry with the given path. For lenient path matching
    use find_traces_by_revision() and filter in Python.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT trace_id, trace_record, vcs, tool, files, trace_timestamp
            FROM traces
            WHERE project_id = %s
              AND vcs->>'revision' = %s
              AND files @> %s::jsonb
            ORDER BY trace_timestamp DESC
            """,
            (
                project_id,
                revision,
                json.dumps([{"path": file_path}]),
            ),
        )
        return [dict(row) for row in cur.fetchall()]


def find_traces_in_time_window(
    project_id: str,
    since: str,
    until: str,
) -> list[dict[str, Any]]:
    """Find all traces in a timestamp window (no file filter).

    Caller should filter by file in Python for lenient path matching.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT trace_id, trace_record, vcs, tool, files, trace_timestamp
            FROM traces
            WHERE project_id = %s
              AND trace_timestamp >= %s
              AND trace_timestamp <= %s
            ORDER BY trace_timestamp DESC
            LIMIT 200
            """,
            (project_id, since, until),
        )
        return [dict(row) for row in cur.fetchall()]


def find_traces_in_window(
    project_id: str,
    file_path: str,
    since: str,
    until: str,
) -> list[dict[str, Any]]:
    """Find traces for a file within a timestamp window.

    Used as the fallback search strategy when neither commit links nor
    exact revision matches are available.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT trace_id, trace_record, vcs, tool, files, trace_timestamp
            FROM traces
            WHERE project_id = %s
              AND trace_timestamp >= %s
              AND trace_timestamp <= %s
              AND files @> %s::jsonb
            ORDER BY trace_timestamp DESC
            LIMIT 100
            """,
            (
                project_id,
                since,
                until,
                json.dumps([{"path": file_path}]),
            ),
        )
        return [dict(row) for row in cur.fetchall()]


def get_commit_links_by_parent(project_id: str, parent_sha: str) -> list[dict[str, Any]]:
    """Find all commit links where parent_sha matches."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, project_id, user_id, commit_sha, parent_sha,
                   trace_ids, files_changed, committed_at, created_at
            FROM commit_links
            WHERE project_id = %s AND parent_sha = %s
            ORDER BY created_at DESC
            """,
            (project_id, parent_sha),
        )
        rows = cur.fetchall()

    return [
        {
            "id": str(row["id"]),
            "project_id": row["project_id"],
            "user_id": row["user_id"],
            "commit_sha": row["commit_sha"],
            "parent_sha": row["parent_sha"],
            "trace_ids": row["trace_ids"],
            "files_changed": row["files_changed"],
            "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]
