"""
Data models for agent-trace-service — pure datastore.

Only models needed by the database layer are defined here.
No domain logic (attribution, scoring, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Org
# ---------------------------------------------------------------------------

@dataclass
class Org:
    id: str
    slug: str
    name: str | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

@dataclass
class TokenContext:
    """Resolved auth context for a presented bearer token."""

    token_id: str
    org_id: str
    project_id_scope: str | None
    scopes: list[str] = field(default_factory=list)


@dataclass
class TokenSummary:
    """Public-facing token metadata (the plaintext token is never returned)."""

    id: str
    org_id: str
    project_id: str | None
    scopes: list[str]
    prefix: str
    name: str | None
    created_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "project_id": self.project_id,
            "scopes": self.scopes,
            "prefix": self.prefix,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

@dataclass
class Project:
    project_id: str
    org_id: str
    id: str | None = None
    name: str | None = None
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id) if self.id else None,
            "org_id": self.org_id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ProjectStats:
    trace_count: int = 0
    conversation_count: int = 0
    unique_users: int = 0
    latest_trace_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_count": self.trace_count,
            "conversation_count": self.conversation_count,
            "unique_users": self.unique_users,
            "latest_trace_at": self.latest_trace_at,
        }


# ---------------------------------------------------------------------------
# Trace (field extraction for indexed columns)
# ---------------------------------------------------------------------------

@dataclass
class TraceFields:
    """Key fields extracted from a trace record, plus the full record."""

    trace_id: str
    version: str
    trace_timestamp: str
    trace_record: str           # JSON-encoded full trace record as-is
    vcs: str | None = None      # JSON-encoded vcs object
    tool: str | None = None     # JSON-encoded tool object
    files: str | None = None    # JSON-encoded files array
    metadata: str | None = None # JSON-encoded metadata object
