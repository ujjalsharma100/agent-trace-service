"""
Database access layer for agent-trace-service — pure datastore operations.

All raw SQL lives here — no other module should import psycopg2 directly
(except init_db.py for schema management).

Every domain helper takes ``org_id`` (and ``project_id`` where relevant) as
explicit parameters; routes derive these from the validated bearer token
and pass them down. There is no implicit ``current_org`` global.

Supports the sync protocol with ``since``/``limit``-based pagination for
every artifact type. NO domain logic (scoring, attribution matching).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import psycopg2
import psycopg2.extras
from flask import g

from model import (
    Org,
    Project,
    ProjectStats,
    TokenContext,
    TokenSummary,
    TraceFields,
)

psycopg2.extras.register_uuid()

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"


class MissingBlobForConversationError(Exception):
    """``content_sha256`` references a row missing from ``blobs`` (chunked sync)."""

    def __init__(self, sha256: str) -> None:
        self.sha256 = sha256
        super().__init__(sha256)


class ProjectExistsError(Exception):
    """``create_project`` raised because (org_id, project_id) already exists."""

    def __init__(self, org_id: str, project_id: str) -> None:
        self.org_id = org_id
        self.project_id = project_id
        super().__init__(f"Project {project_id!r} already exists in org {org_id}")


class ProjectNotFoundError(Exception):
    """Sync attempted against an unregistered (org_id, project_id)."""

    def __init__(self, org_id: str, project_id: str) -> None:
        self.org_id = org_id
        self.project_id = project_id
        super().__init__(f"Project {project_id!r} is not registered in org {org_id}")


class InvalidProjectSlugError(ValueError):
    """``project_id`` failed the slug shape CHECK constraint."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(
            f"Invalid project slug {project_id!r}: must match ^[a-z0-9][a-z0-9._-]{{0,63}}$"
        )


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
# Orgs
# ---------------------------------------------------------------------------

def get_org_by_id(org_id: str) -> Org | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, slug, name, created_at FROM orgs WHERE id = %s", (org_id,))
        row = cur.fetchone()
    if not row:
        return None
    return Org(id=str(row["id"]), slug=row["slug"], name=row["name"], created_at=row["created_at"])


def get_org_by_slug(slug: str) -> Org | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, slug, name, created_at FROM orgs WHERE slug = %s", (slug,))
        row = cur.fetchone()
    if not row:
        return None
    return Org(id=str(row["id"]), slug=row["slug"], name=row["name"], created_at=row["created_at"])


def create_org(slug: str, name: str | None = None, *, org_id: str | None = None) -> Org:
    """Create an org.

    ``org_id`` is optional: when omitted the database generates the UUID
    (the common single-tenant / self-host case). A control plane that mirrors
    its own orgs into this service can pass an explicit ``org_id`` so the two
    systems share one identifier — the column is a plain UUID PK, so an
    explicit value is accepted as-is.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if org_id is not None:
            cur.execute(
                "INSERT INTO orgs (id, slug, name) VALUES (%s, %s, %s) "
                "RETURNING id, slug, name, created_at",
                (org_id, slug, name),
            )
        else:
            cur.execute(
                "INSERT INTO orgs (slug, name) VALUES (%s, %s) "
                "RETURNING id, slug, name, created_at",
                (slug, name),
            )
        row = cur.fetchone()
    return Org(id=str(row["id"]), slug=row["slug"], name=row["name"], created_at=row["created_at"])


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def insert_token(
    *,
    org_id: str,
    project_id: str | None,
    scopes: list[str],
    token_hash: str,
    prefix: str,
    name: str | None,
) -> str:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO tokens (org_id, project_id, scopes, token_hash, prefix, name)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (org_id, project_id, scopes, token_hash, prefix, name),
        )
        row = cur.fetchone()
    return str(row["id"])


def lookup_token_by_hash(token_hash: str) -> TokenContext | None:
    """Return the auth context for an active (not revoked) token hash, else None.

    Also bumps ``last_used_at``.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, org_id, project_id, scopes
            FROM tokens
            WHERE token_hash = %s AND revoked_at IS NULL
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            "UPDATE tokens SET last_used_at = NOW() WHERE id = %s",
            (row["id"],),
        )
    return TokenContext(
        token_id=str(row["id"]),
        org_id=str(row["org_id"]),
        project_id_scope=row["project_id"],
        scopes=list(row["scopes"] or []),
    )


def revoke_token(token_id: str) -> bool:
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE tokens SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL",
            (token_id,),
        )
        return cur.rowcount > 0


def list_tokens(org_id: str | None = None) -> list[TokenSummary]:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if org_id:
            cur.execute(
                """
                SELECT id, org_id, project_id, scopes, prefix, name,
                       created_at, last_used_at, revoked_at
                FROM tokens
                WHERE org_id = %s
                ORDER BY created_at DESC
                """,
                (org_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, org_id, project_id, scopes, prefix, name,
                       created_at, last_used_at, revoked_at
                FROM tokens
                ORDER BY created_at DESC
                """,
            )
        rows = cur.fetchall()
    return [
        TokenSummary(
            id=str(r["id"]),
            org_id=str(r["org_id"]),
            project_id=r["project_id"],
            scopes=list(r["scopes"] or []),
            prefix=r["prefix"],
            name=r["name"],
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
            revoked_at=r["revoked_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def ensure_project(org_id: str, project_id: str) -> None:
    """Idempotent upsert. Retained for tests that need a project to appear
    without going through the registration route. Sync paths use
    ``assert_project_exists`` instead — projects must be explicitly created.
    """
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO projects (org_id, project_id)
            VALUES (%s, %s)
            ON CONFLICT (org_id, project_id) DO NOTHING
            """,
            (org_id, project_id),
        )


def assert_project_exists(org_id: str, project_id: str) -> None:
    """Raise ``ProjectNotFoundError`` if (org_id, project_id) is not registered."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM projects WHERE org_id = %s AND project_id = %s",
            (org_id, project_id),
        )
        if cur.fetchone() is None:
            raise ProjectNotFoundError(org_id, project_id)


def create_project(
    org_id: str,
    project_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Project:
    """Explicit project creation. Raises ``ProjectExistsError`` on conflict.

    Distinct from ``ensure_project`` (idempotent upsert called from sync paths) —
    this is the user-facing ``POST /api/v1/projects`` registration. The slug
    CHECK is enforced in SQL; we surface a typed error so the route can return
    a 400.
    """
    db = get_db()
    try:
        with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO projects (org_id, project_id, name, description)
                VALUES (%s, %s, %s, %s)
                RETURNING id, org_id, project_id, name, description, created_at, updated_at
                """,
                (org_id, project_id, name, description),
            )
            row = cur.fetchone()
    except psycopg2.errors.UniqueViolation:
        db.rollback()
        raise ProjectExistsError(org_id, project_id) from None
    except psycopg2.errors.CheckViolation:
        db.rollback()
        raise InvalidProjectSlugError(project_id) from None
    return Project(
        id=str(row["id"]),
        org_id=str(row["org_id"]),
        project_id=row["project_id"],
        name=row.get("name"),
        description=row.get("description"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def list_projects(org_id: str) -> list[Project]:
    """All projects in an org, newest first."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, org_id, project_id, name, description, created_at, updated_at
            FROM projects WHERE org_id = %s
            ORDER BY created_at DESC
            """,
            (org_id,),
        )
        rows = cur.fetchall()
    return [
        Project(
            id=str(r["id"]),
            org_id=str(r["org_id"]),
            project_id=r["project_id"],
            name=r.get("name"),
            description=r.get("description"),
            created_at=r.get("created_at"),
            updated_at=r.get("updated_at"),
        )
        for r in rows
    ]


def get_project(org_id: str, project_id: str) -> Project | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM projects WHERE org_id = %s AND project_id = %s",
            (org_id, project_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return Project(
        id=str(row["id"]),
        org_id=str(row["org_id"]),
        project_id=row["project_id"],
        name=row.get("name"),
        description=row.get("description"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def delete_project(org_id: str, project_id: str) -> bool:
    """Delete a project and (via ON DELETE CASCADE) all of its trace data.

    Returns ``True`` if a row was removed, ``False`` if ``(org_id, project_id)``
    did not exist. The composite ``(org_id, project_id)`` foreign keys on
    traces / commit_links / conversations / summaries cascade, so this also
    clears the project's stored artifacts.
    """
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM projects WHERE org_id = %s AND project_id = %s",
            (org_id, project_id),
        )
        return cur.rowcount > 0


def get_project_stats(org_id: str, project_id: str) -> ProjectStats:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT COUNT(*) AS count FROM traces WHERE org_id = %s AND project_id = %s",
            (org_id, project_id),
        )
        trace_count = cur.fetchone()["count"]

        cur.execute(
            """
            SELECT trace_timestamp FROM traces
            WHERE org_id = %s AND project_id = %s
            ORDER BY trace_timestamp DESC LIMIT 1
            """,
            (org_id, project_id),
        )
        latest = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(DISTINCT user_id) AS count FROM traces
            WHERE org_id = %s AND project_id = %s
            """,
            (org_id, project_id),
        )
        unique_users = cur.fetchone()["count"]

        cur.execute(
            """
            SELECT COUNT(*) AS count FROM conversation_contents
            WHERE org_id = %s AND project_id = %s
            """,
            (org_id, project_id),
        )
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

def insert_trace(
    org_id: str,
    project_id: str,
    user_id: str,
    fields: TraceFields,
) -> None:
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO traces (
                org_id, project_id, user_id,
                trace_id, version, trace_timestamp,
                vcs, tool, files, metadata,
                trace_record
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s
            ) ON CONFLICT (org_id, project_id, trace_id) DO NOTHING
            """,
            (
                org_id, project_id, user_id,
                fields.trace_id, fields.version, fields.trace_timestamp,
                fields.vcs, fields.tool, fields.files, fields.metadata,
                fields.trace_record,
            ),
        )


def list_traces_since(
    org_id: str,
    project_id: str,
    *,
    since: str = "",
    limit: int = 500,
) -> tuple[list[Any], str | None]:
    """Return traces ingested after ``since`` for ``(org_id, project_id)``.

    ``since`` is compared against ``created_at`` (server ingestion time), not
    ``trace_timestamp`` (originator's clock). This keeps the cursor monotonic
    in server time so a teammate's older-stamped trace pushed today is still
    visible to a peer whose pull cursor advanced past yesterday.
    """
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if since:
            cur.execute(
                """SELECT trace_record, created_at FROM traces
                   WHERE org_id = %s AND project_id = %s AND created_at > %s
                   ORDER BY created_at ASC LIMIT %s""",
                (org_id, project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT trace_record, created_at FROM traces
                   WHERE org_id = %s AND project_id = %s
                   ORDER BY created_at ASC LIMIT %s""",
                (org_id, project_id, limit),
            )
        rows = cur.fetchall()

    items = [r["trace_record"] for r in rows]
    max_ts = rows[-1]["created_at"].isoformat() if rows else None
    return items, max_ts


def get_trace(org_id: str, project_id: str, trace_id: str) -> dict[str, Any] | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT trace_record, user_id FROM traces
               WHERE org_id = %s AND project_id = %s AND trace_id = %s LIMIT 1""",
            (org_id, project_id, trace_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"trace": row["trace_record"], "user_id": row["user_id"]}


# ---------------------------------------------------------------------------
# Ledgers — CRUD + sync pagination
# ---------------------------------------------------------------------------

def upsert_ledger(
    org_id: str,
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
                org_id, project_id, user_id, commit_sha, parent_sha,
                trace_ids, files_changed, committed_at, ledger
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, project_id, commit_sha) DO UPDATE SET
                ledger = EXCLUDED.ledger,
                user_id = EXCLUDED.user_id
            """,
            (
                org_id,
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
    org_id: str,
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
                   WHERE org_id = %s AND project_id = %s AND ledger IS NOT NULL
                     AND created_at > %s
                   ORDER BY created_at ASC LIMIT %s""",
                (org_id, project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT ledger FROM commit_links
                   WHERE org_id = %s AND project_id = %s AND ledger IS NOT NULL
                   ORDER BY created_at ASC LIMIT %s""",
                (org_id, project_id, limit),
            )
        rows = cur.fetchall()

    items = [r["ledger"] for r in rows]
    max_ts = None
    if items:
        last = items[-1]
        if isinstance(last, dict):
            max_ts = last.get("committed_at") or last.get("created_at")
    return items, max_ts


def get_ledger(org_id: str, project_id: str, commit_sha: str) -> dict[str, Any] | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT ledger FROM commit_links
               WHERE org_id = %s AND project_id = %s AND commit_sha = %s
                 AND ledger IS NOT NULL
               LIMIT 1""",
            (org_id, project_id, commit_sha),
        )
        row = cur.fetchone()
    if not row:
        return None
    return row["ledger"]


# ---------------------------------------------------------------------------
# Commit Links — CRUD + sync pagination
# ---------------------------------------------------------------------------

def insert_commit_link(
    org_id: str,
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
                org_id, project_id, user_id, commit_sha, parent_sha,
                trace_ids, files_changed, committed_at, ledger
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, project_id, commit_sha) DO UPDATE SET
                parent_sha    = EXCLUDED.parent_sha,
                trace_ids     = EXCLUDED.trace_ids,
                files_changed = EXCLUDED.files_changed,
                committed_at  = EXCLUDED.committed_at,
                ledger        = COALESCE(EXCLUDED.ledger, commit_links.ledger),
                user_id       = EXCLUDED.user_id
            """,
            (
                org_id,
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
    org_id: str,
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
                   WHERE org_id = %s AND project_id = %s AND created_at > %s
                   ORDER BY created_at ASC LIMIT %s""",
                (org_id, project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT id, project_id, user_id, commit_sha, parent_sha,
                          trace_ids, files_changed, committed_at, created_at
                   FROM commit_links
                   WHERE org_id = %s AND project_id = %s
                   ORDER BY created_at ASC LIMIT %s""",
                (org_id, project_id, limit),
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
# Blobs — content-addressed storage
# ---------------------------------------------------------------------------

def blob_exists(sha256: str) -> bool:
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM blobs WHERE sha256 = %s", (sha256,))
        return cur.fetchone() is not None


def insert_blob(sha256: str, raw: bytes, content_type: str | None = None) -> None:
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO blobs (sha256, size, content_type, bytes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (sha256) DO NOTHING
            """,
            (sha256, len(raw), content_type, psycopg2.Binary(raw)),
        )


def get_blob_bytes(sha256: str) -> bytes | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT bytes FROM blobs WHERE sha256 = %s", (sha256,))
        row = cur.fetchone()
    if not row or row["bytes"] is None:
        return None
    return bytes(row["bytes"])


# ---------------------------------------------------------------------------
# Conversations — CRUD + sync pagination
# ---------------------------------------------------------------------------

def _inline_payload_bytes(item: dict[str, Any]) -> bytes | None:
    """Decode inline conversation body to raw bytes, if present."""
    text = item.get("content")
    if text is not None:
        if not isinstance(text, str):
            return None
        return text.encode("utf-8")
    b64 = item.get("content_b64")
    if b64:
        if not isinstance(b64, str):
            return None
        return base64.b64decode(b64)
    return None


def upsert_conversation_pointer(
    org_id: str,
    project_id: str,
    user_id: str,
    item: dict[str, Any],
) -> None:
    """Insert or update a conversation pointer.

    Accepts both inline (``content`` / ``content_b64``) and chunked
    (``content_sha256`` + ``size``) payloads. Upstream callers may set both
    inline content AND a sha pointer when the blob is small enough to be
    inlined for round-trip simplicity; the schema permits it.

    Inline payloads must satisfy ``conversation_contents.content_sha256`` →
    ``blobs(sha256)``: when inline bytes are present we insert (or dedupe) the
    blob before upserting the row so the foreign key holds.
    """
    conversation_id = item.get("conversation_id")
    if not conversation_id:
        return

    raw = _inline_payload_bytes(item)
    sha_for_row: str | None
    if raw is not None:
        sha_for_row = hashlib.sha256(raw).hexdigest()
        insert_blob(sha_for_row, raw)
    else:
        sha_for_row = item.get("content_sha256")
        if sha_for_row and not blob_exists(sha_for_row):
            raise MissingBlobForConversationError(sha_for_row)

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_contents (
                org_id, project_id, user_id, conversation_id,
                content, content_b64, content_sha256, size
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, project_id, conversation_id) DO UPDATE SET
                content        = EXCLUDED.content,
                content_b64    = EXCLUDED.content_b64,
                content_sha256 = EXCLUDED.content_sha256,
                size           = EXCLUDED.size,
                updated_at     = NOW()
            """,
            (
                org_id,
                project_id,
                user_id,
                conversation_id,
                item.get("content"),
                item.get("content_b64"),
                sha_for_row,
                item.get("size"),
            ),
        )


def list_conversations_since(
    org_id: str,
    project_id: str,
    *,
    since: str = "",
    limit: int = 500,
) -> tuple[list[Any], str | None]:
    """Return conversation pointers newer than ``since``."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if since:
            cur.execute(
                """SELECT conversation_id, content, content_b64, content_sha256, size, updated_at
                   FROM conversation_contents
                   WHERE org_id = %s AND project_id = %s AND updated_at > %s
                   ORDER BY updated_at ASC LIMIT %s""",
                (org_id, project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT conversation_id, content, content_b64, content_sha256, size, updated_at
                   FROM conversation_contents
                   WHERE org_id = %s AND project_id = %s
                   ORDER BY updated_at ASC LIMIT %s""",
                (org_id, project_id, limit),
            )
        rows = cur.fetchall()

    items: list[dict[str, Any]] = []
    max_ts = None
    for row in rows:
        item: dict[str, Any] = {"conversation_id": row["conversation_id"]}
        if row["content"] is not None:
            item["content"] = row["content"]
        if row["content_b64"] is not None:
            item["content_b64"] = row["content_b64"]
        if row["content_sha256"]:
            item["content_sha256"] = row["content_sha256"]
        if row["size"] is not None:
            item["size"] = row["size"]
        if row["updated_at"]:
            item["updated_at"] = row["updated_at"].isoformat()
            max_ts = row["updated_at"].isoformat()
        items.append(item)
    return items, max_ts


def get_conversation_pointer(
    org_id: str,
    project_id: str,
    conversation_id: str,
) -> dict[str, Any] | None:
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT conversation_id, content, content_b64, content_sha256, size
               FROM conversation_contents
               WHERE org_id = %s AND project_id = %s AND conversation_id = %s LIMIT 1""",
            (org_id, project_id, conversation_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "conversation_id": row["conversation_id"],
        "content": row["content"],
        "content_b64": row["content_b64"],
        "content_sha256": row["content_sha256"],
        "size": row["size"],
    }


# ---------------------------------------------------------------------------
# Conversation summaries — CRUD + sync pagination
# ---------------------------------------------------------------------------

def upsert_conversation_summary(
    org_id: str,
    project_id: str,
    user_id: str,
    item: dict[str, Any],
) -> None:
    """Insert or update a conversation summary row.

    Required item fields: ``conversation_id``, ``summary``, ``created_at``.
    Optional: ``session_id``. Upserts on (org, project, conversation_id, created_at).
    """
    conversation_id = item.get("conversation_id")
    summary = item.get("summary")
    created_at = item.get("created_at") or item.get("updated_at")
    if not conversation_id or not summary or not created_at:
        return

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_summaries (
                org_id, project_id, user_id, conversation_id,
                summary, session_id, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, project_id, conversation_id, created_at) DO UPDATE SET
                summary    = EXCLUDED.summary,
                session_id = EXCLUDED.session_id,
                updated_at = NOW()
            """,
            (
                org_id,
                project_id,
                user_id,
                conversation_id,
                summary,
                item.get("session_id"),
                created_at,
            ),
        )


def list_summaries_since(
    org_id: str,
    project_id: str,
    *,
    since: str = "",
    limit: int = 500,
) -> tuple[list[Any], str | None]:
    """Return summary rows newer than ``since`` (by updated_at)."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if since:
            cur.execute(
                """SELECT conversation_id, summary, session_id, created_at, updated_at
                   FROM conversation_summaries
                   WHERE org_id = %s AND project_id = %s AND updated_at > %s
                   ORDER BY updated_at ASC LIMIT %s""",
                (org_id, project_id, since, limit),
            )
        else:
            cur.execute(
                """SELECT conversation_id, summary, session_id, created_at, updated_at
                   FROM conversation_summaries
                   WHERE org_id = %s AND project_id = %s
                   ORDER BY updated_at ASC LIMIT %s""",
                (org_id, project_id, limit),
            )
        rows = cur.fetchall()

    items: list[dict[str, Any]] = []
    max_ts = None
    for row in rows:
        item: dict[str, Any] = {
            "conversation_id": row["conversation_id"],
            "summary": row["summary"],
        }
        if row["session_id"]:
            item["session_id"] = row["session_id"]
        if row["created_at"]:
            item["created_at"] = row["created_at"].isoformat()
        if row["updated_at"]:
            item["updated_at"] = row["updated_at"].isoformat()
            max_ts = row["updated_at"].isoformat()
        items.append(item)
    return items, max_ts
