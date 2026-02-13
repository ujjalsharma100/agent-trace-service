"""
Application / business logic for agent-trace-service.

This module sits between the Flask routes (app.py) and the database layer
(database_service.py).  It owns token management, payload extraction, and
orchestrates multi-step operations.
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
# Configuration (read once at import time)
# ---------------------------------------------------------------------------

AUTH_SECRET = os.environ.get("AUTH_SECRET", "dev-secret")


# ---------------------------------------------------------------------------
# Token helpers (user-based — no project_id in the token)
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    return hmac.new(
        AUTH_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()[:16]


def generate_token(user_id: str) -> str:
    """Create a signed bearer token for *user_id*."""
    raw = json.dumps({"user_id": user_id, "iat": int(time.time())})
    encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return f"{encoded}.{_sign(encoded)}"


def decode_token(token: str) -> str | None:
    """Return the user_id embedded in *token*, or None if invalid."""
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
    """Return a health-check payload (raises on DB failure)."""
    db.check_db_health()
    return {
        "status": "ok",
        "db": "connected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def handle_generate_token(user_id: str) -> dict[str, Any]:
    token = generate_token(user_id)
    return {
        "token": token,
        "user_id": user_id,
        "note": "Store this token securely. Use it as: Authorization: Bearer <token>",
    }


def handle_verify_token(token: str) -> tuple[dict[str, Any], bool]:
    """Return (payload, is_valid)."""
    user_id = decode_token(token)
    if not user_id:
        return {"valid": False, "error": "Invalid token"}, False
    return {"valid": True, "user_id": user_id}, True


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def get_project_detail(project_id: str) -> dict[str, Any] | None:
    """Return project info + stats, or None if not found."""
    project = db.get_project(project_id)
    if not project:
        return None
    stats = db.get_project_stats(project_id)
    return {
        "project": project.to_dict(),
        "stats": stats.to_dict(),
    }


def create_or_update_project(
    project_id: str,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    project = db.upsert_project(project_id, name=name, description=description)
    return {"project": project.to_dict()}


# ---------------------------------------------------------------------------
# Trace field extraction
# ---------------------------------------------------------------------------

def extract_fields(trace: dict[str, Any]) -> TraceFields:
    """
    Extract key fields from a raw trace dict.

    Stores the major sub-objects (vcs, tool, files, metadata) as JSON
    and keeps the full trace record as-is in trace_record.
    """
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


# ---------------------------------------------------------------------------
# Trace ingestion
# ---------------------------------------------------------------------------

def ingest_trace(
    project_id: str,
    user_id: str,
    trace: dict[str, Any],
    conversation_contents: list[dict[str, str]] | None = None,
) -> str:
    """Ingest a single trace. Returns the trace_id."""
    fields = extract_fields(trace)
    db.ensure_project(project_id)
    db.insert_trace(project_id, user_id, fields)

    if conversation_contents:
        db.upsert_conversation_contents(project_id, user_id, conversation_contents)

    return trace["id"]


def sync_conversation_contents(
    project_id: str,
    user_id: str,
    conversation_contents: list[dict[str, str]],
) -> None:
    """Upsert conversation contents only (no trace). Used after agent response is complete."""
    if not conversation_contents:
        return
    db.ensure_project(project_id)
    db.upsert_conversation_contents(project_id, user_id, conversation_contents)


def batch_ingest(
    project_id: str,
    user_id: str,
    items: list[dict[str, Any]],
) -> list[str]:
    """Ingest multiple traces. Returns list of trace_ids."""
    db.ensure_project(project_id)

    trace_ids: list[str] = []
    for item in items:
        trace = item.get("trace", {})
        fields = extract_fields(trace)
        db.insert_trace(project_id, user_id, fields)

        conv_contents = item.get("conversation_contents")
        if conv_contents:
            db.upsert_conversation_contents(project_id, user_id, conv_contents)

        trace_ids.append(trace["id"])

    return trace_ids


# ---------------------------------------------------------------------------
# Trace querying
# ---------------------------------------------------------------------------

def query_traces(
    project_id: str,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    limit = min(limit, 200)
    traces, total = db.list_traces(
        project_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {
        "traces": traces,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_trace_detail(project_id: str, trace_id: str) -> dict[str, Any] | None:
    return db.get_trace(project_id, trace_id)
