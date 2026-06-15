"""
Application logic for agent-trace-service — pure datastore operations.

This module sits between the Flask routes (app.py) and the database layer
(database_service.py).  It owns token issuance / verification and
orchestrates bulk upsert operations for the sync protocol.

NO domain logic (attribution, blame, scoring, summaries) lives here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from model import TokenContext, TraceFields
import database_service as db


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Admin secret guards token issuance / revocation. In dev compose this is
# set explicitly; in production it must be a strong random string.
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "dev-admin-secret")

# Bumped whenever sql/ contents change. Source of truth for /health and
# /api/v1/version. Kept in sync with init_db.SCHEMA_VERSION.
SCHEMA_VERSION = "004-traces-created-at-idx"

# Build SHA — populated by deploy / Dockerfile via env. Falls back to "dev".
BUILD_SHA = os.environ.get("BUILD_SHA", "dev")

# Largest blob accepted by POST /api/v1/blobs (raw body). 10 MiB default.
BLOB_MAX_BYTES = int(os.environ.get("BLOB_MAX_BYTES", str(10 * 1024 * 1024)))

# Token shape: ``at_<32 url-safe chars>``. The 8-char prefix (``at_xxxxx``)
# is stored verbatim for log / UI display; only the SHA-256 of the full
# token is persisted.
TOKEN_PREFIX_PLAIN = "at_"
TOKEN_DISPLAY_PREFIX_LEN = 8


# ---------------------------------------------------------------------------
# Token helpers (opaque, hash-stored)
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mint_token() -> str:
    return f"{TOKEN_PREFIX_PLAIN}{secrets.token_urlsafe(32)}"


def issue_token(
    *,
    org_id: str,
    project_id: str | None,
    scopes: list[str] | None,
    name: str | None,
) -> dict[str, Any]:
    """Mint and persist a new opaque token. Returns the plaintext exactly once."""
    token = _mint_token()
    token_hash = _hash_token(token)
    prefix = token[:TOKEN_DISPLAY_PREFIX_LEN]
    eff_scopes = list(scopes) if scopes else ["read", "write"]
    token_id = db.insert_token(
        org_id=org_id,
        project_id=project_id,
        scopes=eff_scopes,
        token_hash=token_hash,
        prefix=prefix,
        name=name,
    )
    return {
        "id": token_id,
        "token": token,
        "prefix": prefix,
        "org_id": org_id,
        "project_id": project_id,
        "scopes": eff_scopes,
        "name": name,
        "note": (
            "Store this token securely — the plaintext is shown exactly once. "
            "Use it as: Authorization: Bearer <token>."
        ),
    }


def verify_token(token: str) -> tuple[dict[str, Any], bool]:
    if not token or not token.startswith(TOKEN_PREFIX_PLAIN):
        return {"valid": False, "error": "Invalid token format"}, False
    ctx = db.lookup_token_by_hash(_hash_token(token))
    if ctx is None:
        return {"valid": False, "error": "Invalid or revoked token"}, False
    # ``org_slug`` is included so CLI clients can verify the URL's
    # ``<org_slug>/<project_slug>`` matches the token's actual scope without
    # leaking other org metadata.
    org = db.get_org_by_id(ctx.org_id)
    return {
        "valid": True,
        "org_id": ctx.org_id,
        "org_slug": org.slug if org else None,
        "project_id": ctx.project_id_scope,
        "scopes": ctx.scopes,
    }, True


def whoami(ctx: TokenContext) -> dict[str, Any]:
    """Return the resolved auth context shape used by ``GET /api/v1/auth/whoami``.

    Distinct from ``verify_token`` (which takes a plaintext body and walks the
    same lookup) — this one takes an already-resolved ``TokenContext`` so the
    route handler can reuse ``g.token_ctx`` without a second hash lookup.
    """
    org = db.get_org_by_id(ctx.org_id)
    return {
        "org_id": ctx.org_id,
        "org_slug": org.slug if org else None,
        "project_id_scope": ctx.project_id_scope,
        "scopes": ctx.scopes,
    }


def resolve_token(token: str) -> TokenContext | None:
    if not token or not token.startswith(TOKEN_PREFIX_PLAIN):
        return None
    return db.lookup_token_by_hash(_hash_token(token))


# ---------------------------------------------------------------------------
# Health / version
# ---------------------------------------------------------------------------

def health_check() -> dict[str, Any]:
    db.check_db_health()
    return {
        "status": "ok",
        "db": "connected",
        "schema_version": SCHEMA_VERSION,
        "build": BUILD_SHA,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def version_info() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "build": BUILD_SHA,
        "name": "agent-trace-service",
    }


# ---------------------------------------------------------------------------
# Project guard
# ---------------------------------------------------------------------------

class OrgSlugMismatchError(Exception):
    """Caller-supplied ``org_slug`` doesn't match the slug owning the request.

    Raised whenever a body / URL ``org_slug`` is checked against the slug of
    the org the bearer token (or the resolved admin ``org_id``) actually
    points at. Carries both sides so the route can return a structured 403.
    """

    def __init__(self, expected: str, got: str) -> None:
        self.expected = expected
        self.got = got
        super().__init__(
            f"org_slug {got!r} does not match the caller's org slug {expected!r}"
        )


class ProjectScopeMismatchError(Exception):
    """``project_id`` in the request lies outside a project-scoped token's reach."""

    def __init__(self, scope: str, got: str) -> None:
        self.scope = scope
        self.got = got
        super().__init__(
            f"project_id {got!r} is outside this token's scope {scope!r}"
        )


def assert_project_in_token_scope(ctx: TokenContext, project_id: str) -> bool:
    """Project-scoped tokens may only act on their bound project."""
    if ctx.project_id_scope is None:
        return True
    return ctx.project_id_scope == project_id


def can_create_project(ctx: TokenContext) -> bool:
    """Project registration requires an org-scoped token (not project-scoped)
    that carries the ``projects:write`` scope. Project-scoped tokens are
    intentionally barred — they can only act within an already-existing
    project. The admin-secret path (``X-Admin-Secret``) is handled at the
    route layer and bypasses this check.
    """
    if ctx.project_id_scope is not None:
        return False
    return "projects:write" in ctx.scopes


# ---------------------------------------------------------------------------
# Sync — Traces
# ---------------------------------------------------------------------------

def _extract_fields(trace: dict[str, Any]) -> TraceFields:
    """Extract indexed fields from a raw trace dict."""
    vcs = trace.get("vcs")
    tool = trace.get("tool")
    files = trace.get("files")
    metadata = trace.get("metadata")

    return TraceFields(
        trace_id=trace["id"],
        version=trace.get("version", "1.0"),
        trace_timestamp=trace["timestamp"],
        vcs=json.dumps(vcs) if vcs else None,
        tool=json.dumps(tool) if tool else None,
        files=json.dumps(files) if files else None,
        metadata=json.dumps(metadata) if metadata else None,
        trace_record=json.dumps(trace),
    )


def sync_upsert_traces(
    org_id: str,
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of traces.  Returns count of items processed."""
    if not items:
        return 0
    db.assert_project_exists(org_id, project_id)
    for item in items:
        if not item.get("id") or not item.get("timestamp"):
            continue
        fields = _extract_fields(item)
        db.insert_trace(org_id, project_id, user_id, fields)
    return len(items)


# ---------------------------------------------------------------------------
# Sync — Ledgers
# ---------------------------------------------------------------------------

def sync_upsert_ledgers(
    org_id: str,
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of ledgers.  Returns count of items processed."""
    if not items:
        return 0
    db.assert_project_exists(org_id, project_id)
    for item in items:
        commit_sha = item.get("commit_sha")
        if not commit_sha:
            continue
        db.upsert_ledger(org_id, project_id, user_id, commit_sha, item)
    return len(items)


# ---------------------------------------------------------------------------
# Sync — Commit Links
# ---------------------------------------------------------------------------

def sync_upsert_commit_links(
    org_id: str,
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of commit links.  Returns count of items processed."""
    if not items:
        return 0
    db.assert_project_exists(org_id, project_id)
    for item in items:
        commit_sha = item.get("commit_sha")
        if not commit_sha:
            continue
        db.insert_commit_link(
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            commit_sha=commit_sha,
            parent_sha=item.get("parent_sha"),
            trace_ids=item.get("trace_ids", []),
            files_changed=item.get("files_changed"),
            committed_at=item.get("committed_at"),
            ledger=item.get("ledger"),
        )
    return len(items)


# ---------------------------------------------------------------------------
# Sync — Conversations
# ---------------------------------------------------------------------------

def sync_upsert_conversations(
    org_id: str,
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of conversation pointers / inline content.

    Items must carry a ``conversation_id`` (sha256 over the original local
    transcript URL). Payload may include any of:
      - ``content``        — inline UTF-8 text (small blobs)
      - ``content_b64``    — inline base64 (binary small blobs)
      - ``content_sha256`` — pointer to a previously-uploaded blob
    """
    if not items:
        return 0
    db.assert_project_exists(org_id, project_id)
    for item in items:
        if not item.get("conversation_id"):
            continue
        db.upsert_conversation_pointer(org_id, project_id, user_id, item)
    return len(items)


def sync_upsert_summaries(
    org_id: str,
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of conversation summary rows.

    Items require ``conversation_id``, ``summary``, ``created_at``; ``session_id``
    is optional. Upserts on (org, project, conversation_id, created_at).
    """
    if not items:
        return 0
    db.assert_project_exists(org_id, project_id)
    for item in items:
        if not item.get("conversation_id") or not item.get("summary"):
            continue
        if not (item.get("created_at") or item.get("updated_at")):
            continue
        db.upsert_conversation_summary(org_id, project_id, user_id, item)
    return len(items)


# ---------------------------------------------------------------------------
# Blobs (content-addressed)
# ---------------------------------------------------------------------------

def store_blob(raw: bytes) -> dict[str, Any]:
    """Store a blob keyed by its SHA-256. Idempotent."""
    if len(raw) > BLOB_MAX_BYTES:
        raise ValueError(f"blob too large: {len(raw)} > {BLOB_MAX_BYTES}")
    sha = hashlib.sha256(raw).hexdigest()
    db.insert_blob(sha, raw)
    return {"sha256": sha, "size": len(raw)}


def fetch_blob(sha256: str) -> bytes | None:
    return db.get_blob_bytes(sha256)


def blob_present(sha256: str) -> bool:
    return db.blob_exists(sha256)
