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
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from model import AttributionResult, TraceFields
import attribution as attr
import database_service as db

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Commit links
# ---------------------------------------------------------------------------

def ingest_commit_link(
    project_id: str,
    user_id: str,
    commit_link_data: dict[str, Any],
) -> str:
    """Validate and store a commit-trace link.  Returns the commit_sha."""
    commit_sha = commit_link_data.get("commit_sha", "")
    parent_sha = commit_link_data.get("parent_sha")
    trace_ids = commit_link_data.get("trace_ids", [])
    files_changed = commit_link_data.get("files_changed")
    committed_at = commit_link_data.get("committed_at")
    ledger = commit_link_data.get("ledger")

    if not commit_sha:
        raise ValueError("commit_sha is required")
    if not isinstance(trace_ids, list) or not trace_ids:
        raise ValueError("trace_ids must be a non-empty list")

    db.ensure_project(project_id)
    db.insert_commit_link(
        project_id=project_id,
        user_id=user_id,
        commit_sha=commit_sha,
        parent_sha=parent_sha,
        trace_ids=trace_ids,
        files_changed=files_changed,
        committed_at=committed_at,
        ledger=ledger,
    )
    return commit_sha


def get_commit_link_detail(
    project_id: str,
    commit_sha: str,
) -> dict[str, Any] | None:
    """Return commit link with expanded trace data."""
    link = db.get_commit_link(project_id, commit_sha)
    if not link:
        return None

    # Enrich with trace summaries for each linked trace
    trace_summaries = []
    for tid in link.get("trace_ids", []):
        trace_data = db.get_trace(project_id, tid)
        if trace_data:
            trace_record = trace_data["trace"]
            # trace_record is the full JSON dict (auto-decoded by psycopg2 JSONB)
            summary: dict[str, Any] = {"trace_id": tid}
            if isinstance(trace_record, dict):
                summary["timestamp"] = trace_record.get("timestamp")
                summary["tool"] = trace_record.get("tool")
                # Extract model from first conversation contributor
                for fe in trace_record.get("files", []):
                    for conv in fe.get("conversations", []):
                        contributor = conv.get("contributor", {})
                        if contributor.get("model_id"):
                            summary["model_id"] = contributor["model_id"]
                            break
                    if "model_id" in summary:
                        break
            trace_summaries.append(summary)
        else:
            trace_summaries.append({"trace_id": tid, "found": False})

    link["trace_summaries"] = trace_summaries
    return link


# ---------------------------------------------------------------------------
# Blame
# ---------------------------------------------------------------------------

MAX_CONVERSATION_SUMMARY_LEN = 200


def blame_file(
    project_id: str,
    file_path: str,
    blame_data: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run attribution for each blame segment and return aggregated results.

    Parameters
    ----------
    project_id : str
        The project to search for traces in.
    file_path : str
        Path of the file being blamed.
    blame_data : list[dict]
        List of blame segments from the client.  Each segment has:
          - start_line (int)
          - end_line (int)
          - commit_sha (str)
          - parent_sha (str | None)
          - content_hash (str | None)
          - timestamp (str | None)  — ISO-8601 author date of the commit

    Returns
    -------
    dict
        {
            "file_path": "...",
            "attributions": [
                {
                    "start_line": N,
                    "end_line": M,
                    "tier": 1-6 or null,
                    "confidence": 0.0-1.0,
                    "trace_id": "..." or null,
                    "contributor": {"type": "ai", "model_id": "..."},
                    "conversation_url": "..." or null,
                    "conversation_summary": "first N chars..." or null,
                    "tool": {...} or null,
                    "signals": [...],
                    "commit_link_match": bool,
                    "content_hash_match": bool,
                }
            ]
        }
    """
    # Attribute each segment — one attribution per blame segment.
    # Within a segment all lines share the same commit, so we attribute at
    # the segment level rather than per-line (the commit SHA is the same for
    # every line in the segment).
    raw_results: list[tuple[dict[str, Any], AttributionResult]] = []

    for segment in blame_data:
        start_line = segment.get("start_line")
        end_line = segment.get("end_line")
        commit_sha = segment.get("commit_sha", "")
        parent_sha = segment.get("parent_sha")
        content_hash = segment.get("content_hash")
        timestamp = segment.get("timestamp")

        if start_line is None or end_line is None or not commit_sha:
            logger.debug("Skipping incomplete blame segment: %s", segment)
            continue

        # --- Ledger-first path ---
        ledger = db.get_ledger(project_id, commit_sha)
        if ledger:
            result = attr.attribute_line(
                project_id=project_id,
                file_path=file_path,
                line_number=(start_line + end_line) // 2,
                blame_commit=commit_sha,
                blame_parent=parent_sha,
                content_hash=content_hash,
                blame_timestamp=timestamp,
                ledger=ledger,
            )
            raw_results.append((segment, result))
            continue

        # Use the midpoint of the range as the representative line
        # (the attribution engine checks range containment, so any line
        # within the range produces the same result for the same trace).
        representative_line = (start_line + end_line) // 2

        result = attr.attribute_line(
            project_id=project_id,
            file_path=file_path,
            line_number=representative_line,
            blame_commit=commit_sha,
            blame_parent=parent_sha,
            content_hash=content_hash,
            blame_timestamp=timestamp,
        )
        raw_results.append((segment, result))

    # Group adjacent segments with the same attribution
    attributions = _merge_attributions(raw_results)

    return {
        "file_path": file_path,
        "attributions": attributions,
    }


def _merge_attributions(
    raw_results: list[tuple[dict[str, Any], AttributionResult]],
) -> list[dict[str, Any]]:
    """Merge adjacent blame segments that share the same attribution into
    contiguous ranges.

    Two segments are merged when they are adjacent (prev.end_line + 1 ==
    next.start_line) *and* they attributed to the same trace_id with the
    same tier.
    """
    if not raw_results:
        return []

    merged: list[dict[str, Any]] = []

    for segment, result in raw_results:
        entry = _format_attribution(segment, result)

        if merged:
            prev = merged[-1]
            # Merge if adjacent and same attribution identity
            if (
                prev["end_line"] + 1 >= entry["start_line"]
                and prev["trace_id"] == entry["trace_id"]
                and prev["tier"] == entry["tier"]
            ):
                prev["end_line"] = entry["end_line"]
                continue

        merged.append(entry)

    return merged


def _format_attribution(
    segment: dict[str, Any],
    result: AttributionResult,
) -> dict[str, Any]:
    """Format a single attribution result for the API response."""
    entry: dict[str, Any] = {
        "start_line": segment.get("start_line"),
        "end_line": segment.get("end_line"),
        "tier": result.tier,
        "confidence": result.confidence,
        "trace_id": result.trace_id,
    }

    # Add contributor info (nested and top-level for CLI/display)
    if result.contributor_type or result.model_id:
        contributor: dict[str, Any] = {}
        if result.contributor_type:
            contributor["type"] = result.contributor_type
        if result.model_id:
            contributor["model_id"] = result.model_id
        entry["contributor"] = contributor
        # Top-level model_id / contributor_type so blame output shows model
        if result.model_id:
            entry["model_id"] = result.model_id
        if result.contributor_type:
            entry["contributor_type"] = result.contributor_type

    # Conversation info
    if result.conversation_url:
        entry["conversation_url"] = result.conversation_url
    if result.conversation_content:
        summary = result.conversation_content[:MAX_CONVERSATION_SUMMARY_LEN]
        if len(result.conversation_content) > MAX_CONVERSATION_SUMMARY_LEN:
            summary += "..."
        entry["conversation_summary"] = summary

    # Tool info
    if result.tool:
        entry["tool"] = result.tool

    # Signals & match flags
    entry["signals"] = result.signals
    entry["commit_link_match"] = result.commit_link_match
    entry["content_hash_match"] = result.content_hash_match

    return entry
