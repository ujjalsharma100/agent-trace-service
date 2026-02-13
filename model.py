"""
Data models for agent-trace-service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

@dataclass
class Project:
    project_id: str
    id: str | None = None
    name: str | None = None
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id) if self.id else None,
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
# Trace
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
