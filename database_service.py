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
