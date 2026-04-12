"""
Database access layer for agent-trace-service — pure datastore operations.

All raw SQL lives here — no other module should import psycopg2 directly
(except init_db.py for schema management).

Supports the sync protocol with ``since``/``limit``-based pagination for
every artifact type.  NO domain logic (scoring, attribution matching).
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg2
import psycopg2.extras
from flask import g

from model import Project, ProjectStats, TraceFields

psycopg2.extras.register_uuid()


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _build_database_url() -> str:
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    user = os.environ.get("DB_USER", "postgres")
    password = os.environ.get("DB_PASSWORD", "postgres")
    name = os.environ.get("DB_NAME", "agent_trace")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(_build_database_url())
        g.db.autocommit = False
    return g.db


def close_db(exc):
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
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT 1")
    return True


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def ensure_project(project_id: str) -> None:
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (project_id) VALUES (%s) ON CONFLICT (project_id) DO NOTHING",
            (project_id,),
        )


def get_project(project_id: str) -> Project | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM projects WHERE project_id = %s", (project_id,))
        row = cur.fetchone()
    if not row:
        return None
    return Project(
        id=str(row["id"]),
        project_id=row["project_id"],
        name=row.get("name"),
        description=row.get("description"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def get_project_stats(project_id: str) -> ProjectStats:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS count FROM traces WHERE project_id = %s", (project_id,))
        trace_count = cur.fetchone()["count"]

        cur.execute(
            "SELECT trace_timestamp FROM traces WHERE project_id = %s ORDER BY trace_timestamp DESC LIMIT 1",
            (project_id,),
        )
        latest = cur.fetchone()

        cur.execute("SELECT COUNT(DISTINCT user_id) AS count FROM traces WHERE project_id = %s", (project_id,))
        unique_users = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) AS count FROM conversation_contents WHERE project_id = %s", (project_id,))
        conv_count = cur.fetchone()["count"]

    return ProjectStats(
        trace_count=trace_count,
        conversation_count=conv_count,
        unique_users=unique_users,
        latest_trace_at=latest["trace_timestamp"].isoformat() if latest else None,
    )


# ---------------------------------------------------------------------------
# Traces — CRUD + sync pagination
# ---------------------------------------------------------------------------

def insert_trace(project_id: str, user_id: str, fields: TraceFields) -> None:
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


def list_traces_since(
    project_id: str,
    *,
    since: str = "",
    limit: int = 500,
) -> tuple[list[Any], str | None]:
    """Return traces newer than ``since``, ordered by timestamp.

    Returns ``(items, max_timestamp)`` for cursor advancement.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if since:
            cur.execute(
                """SELECT trace_record FROM traces
                   WHERE project_id = %s AND trace_timestamp > %s
                   ORDER BY trace_timestamp ASC LIMIT %s""",
                (project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT trace_record FROM traces
                   WHERE project_id = %s
                   ORDER BY trace_timestamp ASC LIMIT %s""",
                (project_id, limit),
            )
        rows = cur.fetchall()

    items = [r["trace_record"] for r in rows]
    max_ts = None
    if items:
        last = items[-1]
        if isinstance(last, dict):
            max_ts = last.get("timestamp")
        elif isinstance(last, str):
            try:
                max_ts = json.loads(last).get("timestamp")
            except (json.JSONDecodeError, AttributeError):
                pass
    return items, max_ts


def get_trace(project_id: str, trace_id: str) -> dict[str, Any] | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT trace_record, user_id FROM traces WHERE project_id = %s AND trace_id = %s LIMIT 1",
            (project_id, trace_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"trace": row["trace_record"], "user_id": row["user_id"]}


# ---------------------------------------------------------------------------
# Ledgers — CRUD + sync pagination
# ---------------------------------------------------------------------------

def upsert_ledger(
    project_id: str,
    user_id: str,
    commit_sha: str,
    ledger: dict[str, Any],
) -> None:
    """Store a ledger.  Uses commit_links table with the ledger JSONB column."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commit_links (
                project_id, user_id, commit_sha, parent_sha,
                trace_ids, files_changed, committed_at, ledger
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, commit_sha) DO UPDATE SET
                ledger = EXCLUDED.ledger,
                user_id = EXCLUDED.user_id
            """,
            (
                project_id,
                user_id,
                commit_sha,
                ledger.get("parent_sha"),
                json.dumps(ledger.get("trace_ids", [])),
                json.dumps(ledger.get("files_changed")) if ledger.get("files_changed") else None,
                ledger.get("committed_at"),
                json.dumps(ledger),
            ),
        )


def list_ledgers_since(
    project_id: str,
    *,
    since: str = "",
    limit: int = 500,
) -> tuple[list[Any], str | None]:
    """Return ledgers newer than ``since``."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if since:
            cur.execute(
                """SELECT ledger FROM commit_links
                   WHERE project_id = %s AND ledger IS NOT NULL
                     AND created_at > %s
                   ORDER BY created_at ASC LIMIT %s""",
                (project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT ledger FROM commit_links
                   WHERE project_id = %s AND ledger IS NOT NULL
                   ORDER BY created_at ASC LIMIT %s""",
                (project_id, limit),
            )
        rows = cur.fetchall()

    items = [r["ledger"] for r in rows]
    max_ts = None
    if items:
        last = items[-1]
        if isinstance(last, dict):
            max_ts = last.get("committed_at") or last.get("created_at")
    return items, max_ts


def get_ledger(project_id: str, commit_sha: str) -> dict[str, Any] | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT ledger FROM commit_links
               WHERE project_id = %s AND commit_sha = %s AND ledger IS NOT NULL
               LIMIT 1""",
            (project_id, commit_sha),
        )
        row = cur.fetchone()
    if not row:
        return None
    return row["ledger"]


# ---------------------------------------------------------------------------
# Commit Links — CRUD + sync pagination
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
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commit_links (
                project_id, user_id, commit_sha, parent_sha,
                trace_ids, files_changed, committed_at, ledger
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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


def list_commit_links_since(
    project_id: str,
    *,
    since: str = "",
    limit: int = 500,
) -> tuple[list[Any], str | None]:
    """Return commit links newer than ``since``."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if since:
            cur.execute(
                """SELECT id, project_id, user_id, commit_sha, parent_sha,
                          trace_ids, files_changed, committed_at, created_at
                   FROM commit_links
                   WHERE project_id = %s AND created_at > %s
                   ORDER BY created_at ASC LIMIT %s""",
                (project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT id, project_id, user_id, commit_sha, parent_sha,
                          trace_ids, files_changed, committed_at, created_at
                   FROM commit_links
                   WHERE project_id = %s
                   ORDER BY created_at ASC LIMIT %s""",
                (project_id, limit),
            )
        rows = cur.fetchall()

    items = []
    max_ts = None
    for row in rows:
        item = {
            "commit_sha": row["commit_sha"],
            "parent_sha": row["parent_sha"],
            "trace_ids": row["trace_ids"],
            "files_changed": row["files_changed"],
            "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        items.append(item)
        max_ts = item["created_at"]

    return items, max_ts


# ---------------------------------------------------------------------------
# Conversations — CRUD + sync pagination
# ---------------------------------------------------------------------------

def upsert_conversation_contents(
    project_id: str,
    user_id: str,
    contents: list[dict[str, str]],
) -> None:
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
                (project_id, user_id, item.get("url", ""), item.get("content", "")),
            )


def list_conversations_since(
    project_id: str,
    *,
    since: str = "",
    limit: int = 500,
) -> tuple[list[Any], str | None]:
    """Return conversation contents newer than ``since``."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if since:
            cur.execute(
                """SELECT url, content, updated_at FROM conversation_contents
                   WHERE project_id = %s AND updated_at > %s
                   ORDER BY updated_at ASC LIMIT %s""",
                (project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT url, content, updated_at FROM conversation_contents
                   WHERE project_id = %s
                   ORDER BY updated_at ASC LIMIT %s""",
                (project_id, limit),
            )
        rows = cur.fetchall()

    items = []
    max_ts = None
    for row in rows:
        item = {
            "url": row["url"],
            "content": row["content"],
        }
        items.append(item)
        if row["updated_at"]:
            max_ts = row["updated_at"].isoformat()

    return items, max_ts


def get_conversation_content(project_id: str, url: str) -> str | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT content FROM conversation_contents WHERE project_id = %s AND url = %s LIMIT 1",
            (project_id, url),
        )
        row = cur.fetchone()
    return row["content"] if row else None
