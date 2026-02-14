"""
Data models for agent-trace-service.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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


# ---------------------------------------------------------------------------
# Commit Link
# ---------------------------------------------------------------------------

@dataclass
class CommitLink:
    """Maps a git commit to the AI traces that contributed to it.

    Created by the post-commit hook to establish a provable link between
    a commit and the traces that were active when it was created.
    """

    project_id: str
    commit_sha: str
    parent_sha: str | None
    trace_ids: list[str]
    files_changed: list[str] | None = None
    user_id: str | None = None
    committed_at: str | None = None
    id: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ---------------------------------------------------------------------------
# Attribution Result
# ---------------------------------------------------------------------------

@dataclass
class AttributionResult:
    """Result of attributing a code line/range to an AI trace.

    Produced by the attribution engine. The tier (1-6) reflects the strength
    of evidence linking the code to a specific AI conversation.
    """

    tier: int | None                      # 1-6, None if no attribution
    confidence: float                     # 0.0 - 1.0
    trace_id: str | None
    conversation_url: str | None
    conversation_content: str | None      # full transcript if available
    contributor_type: str | None          # "ai", "human", "mixed", "unknown"
    model_id: str | None                  # e.g. "anthropic/claude-sonnet-4"
    tool: dict | None                     # e.g. {"name": "cursor", "version": "..."}
    matched_range: dict | None            # {"start_line": N, "end_line": M}
    content_hash_match: bool
    commit_link_match: bool
    signals: list[str]                    # ["commit_link", "content_hash", ...]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "tier": self.tier,
            "confidence": self.confidence,
            "trace_id": self.trace_id,
            "contributor_type": self.contributor_type,
            "model_id": self.model_id,
            "content_hash_match": self.content_hash_match,
            "commit_link_match": self.commit_link_match,
            "signals": self.signals,
        }
        if self.conversation_url is not None:
            d["conversation_url"] = self.conversation_url
        if self.conversation_content is not None:
            d["conversation_content"] = self.conversation_content
        if self.tool is not None:
            d["tool"] = self.tool
        if self.matched_range is not None:
            d["matched_range"] = self.matched_range
        return d
