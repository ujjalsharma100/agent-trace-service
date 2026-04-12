"""
Application logic for agent-trace-service — pure datastore operations.

This module sits between the Flask routes (app.py) and the database layer
(database_service.py).  It owns token management and orchestrates bulk
upsert operations for the sync protocol.

NO domain logic (attribution, blame, scoring, summaries) lives here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from model import TraceFields
import database_service as db


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUTH_SECRET = os.environ.get("AUTH_SECRET", "dev-secret")


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    return hmac.new(
        AUTH_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()[:16]


def generate_token(user_id: str) -> str:
    raw = json.dumps({"user_id": user_id, "iat": int(time.time())})
    encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return f"{encoded}.{_sign(encoded)}"


def decode_token(token: str) -> str | None:
    try:
        encoded, sig = token.split(".", 1)
        if _sign(encoded) != sig:
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return payload.get("user_id")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def health_check() -> dict[str, Any]:
    db.check_db_health()
    return {
        "status": "ok",
        "db": "connected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Tokens (public API)
# ---------------------------------------------------------------------------

def handle_generate_token(user_id: str) -> dict[str, Any]:
    token = generate_token(user_id)
    return {
        "token": token,
        "user_id": user_id,
        "note": "Store this token securely. Use it as: Authorization: Bearer <token>",
    }


def handle_verify_token(token: str) -> tuple[dict[str, Any], bool]:
    user_id = decode_token(token)
    if not user_id:
        return {"valid": False, "error": "Invalid token"}, False
    return {"valid": True, "user_id": user_id}, True


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
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of traces.  Returns count of items processed."""
    if not items:
        return 0
    db.ensure_project(project_id)
    for item in items:
        if not item.get("id") or not item.get("timestamp"):
            continue
        fields = _extract_fields(item)
        db.insert_trace(project_id, user_id, fields)
    return len(items)


# ---------------------------------------------------------------------------
# Sync — Ledgers
# ---------------------------------------------------------------------------

def sync_upsert_ledgers(
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of ledgers.  Returns count of items processed."""
    if not items:
        return 0
    db.ensure_project(project_id)
    for item in items:
        commit_sha = item.get("commit_sha")
        if not commit_sha:
            continue
        db.upsert_ledger(project_id, user_id, commit_sha, item)
    return len(items)


# ---------------------------------------------------------------------------
# Sync — Commit Links
# ---------------------------------------------------------------------------

def sync_upsert_commit_links(
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> int:
    """Upsert a batch of commit links.  Returns count of items processed."""
    if not items:
        return 0
    db.ensure_project(project_id)
    for item in items:
        commit_sha = item.get("commit_sha")
        if not commit_sha:
            continue
        db.insert_commit_link(
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
    project_id: str,
    user_id: str,
    items: list[dict[str, str]],
) -> int:
    """Upsert a batch of conversation contents.  Returns count."""
    if not items:
        return 0
    db.ensure_project(project_id)
    db.upsert_conversation_contents(project_id, user_id, items)
    return len(items)
