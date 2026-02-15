"""
Attribution engine for agent-trace-service.

This is the intellectual center of the AI-blame feature.  Given git-blame data
for a line (which commit introduced it, parent commit, content hash, timestamp)
it scores candidate traces and assigns a confidence tier (1-6) expressing how
certain we are that the line originated from an AI conversation.

Tier definitions:
  1  Provably certain   (100%)   — commit link + content hash + blame + range
  2  Effectively certain (99.9%) — inferred link (parent revision match) + hash
  3  Very high confidence (95%+) — ancestor revision + hash
  4  High confidence     (85%+)  — revision match, range overlap, no hash match
  5  Medium confidence   (60-85%)— file match, timestamp, partial overlap
  6  Suggestive          (<60%)  — same file, general time period
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import database_service as db
from model import AttributionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal weights — used by _score_trace to produce a numeric score
# ---------------------------------------------------------------------------

WEIGHT_COMMIT_LINK = 40    # trace ID appears in commit link's trace_ids
WEIGHT_CONTENT_HASH = 30   # content hash of blamed lines matches trace hash
WEIGHT_REVISION_PARENT = 15  # trace vcs.revision == blame parent commit
WEIGHT_REVISION_ANCESTOR = 8  # trace vcs.revision is ancestor of blame commit
WEIGHT_RANGE_MATCH = 10    # blamed line falls within trace's recorded range
WEIGHT_RANGE_OVERLAP = 5   # blamed line is near (but not inside) trace range
WEIGHT_TIMESTAMP = 5       # trace timestamp falls within commit window


# ---------------------------------------------------------------------------
# Tier thresholds — score -> tier mapping
# ---------------------------------------------------------------------------

def _compute_tier(score: float, signals: list[str]) -> int | None:
    """Map a numeric score + signal list to a confidence tier (1-6) or None.

    Requires at least one *structural* signal (commit_link, content_hash,
    revision_parent, revision_ancestor, range_match, range_overlap).
    Timestamp alone is never sufficient — it would false-positive on every
    manual edit made within the same 24-hour window as any AI trace.
    """
    if score <= 0:
        return None

    # Require at least one structural signal beyond just timestamp
    _STRUCTURAL = {
        "commit_link", "content_hash", "revision_parent",
        "revision_ancestor", "range_match", "range_overlap",
    }
    if not any(s in _STRUCTURAL for s in signals):
        return None

    # Tier 1 requires both commit_link AND content_hash signals
    if score >= 95 and "commit_link" in signals and "content_hash" in signals:
        return 1

    if score >= 80:
        return 2
    if score >= 60:
        return 3
    if score >= 45:
        return 4
    if score >= 25:
        return 5
    return 6


def _tier_to_confidence(tier: int | None) -> float:
    """Convert a tier to a representative confidence value."""
    if tier is None:
        return 0.0
    return {
        1: 1.0,
        2: 0.999,
        3: 0.95,
        4: 0.85,
        5: 0.70,
        6: 0.40,
    }.get(tier, 0.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def attribute_line(
    project_id: str,
    file_path: str,
    line_number: int,
    blame_commit: str,
    blame_parent: str | None,
    content_hash: str | None,
    blame_timestamp: str | None,
) -> AttributionResult:
    """Attribute a single line of code to an AI trace.

    Parameters
    ----------
    project_id : str
        The project that owns the traces.
    file_path : str
        Path of the file being blamed (as stored in traces).
    line_number : int
        The 1-based line number to attribute.
    blame_commit : str
        The commit SHA that ``git blame`` says introduced this line.
    blame_parent : str | None
        Parent of *blame_commit* (``git rev-parse <commit>^``).
    content_hash : str | None
        SHA-256 prefix of the normalized content of the blamed lines.
    blame_timestamp : str | None
        ISO-8601 author date of *blame_commit*.

    Returns
    -------
    AttributionResult
        Contains the best matching trace, tier, confidence, and signals.
    """

    # --- Signal 1: Commit link lookup ---
    commit_link = db.get_commit_link(project_id, blame_commit)
    linked_trace_ids: list[str] = (
        commit_link["trace_ids"] if commit_link else []
    )

    # --- Signal 2: Find candidate traces ---
    candidates = _find_candidate_traces(
        project_id,
        file_path,
        blame_commit,
        blame_parent,
        blame_timestamp,
        linked_trace_ids,
    )

    if not candidates:
        return _no_attribution()

    # --- Signal 3: Score each candidate ---
    best_score: float = 0.0
    best_trace: dict[str, Any] | None = None
    best_signals: list[str] = []

    for trace in candidates:
        score, trace_signals = _score_trace(
            trace,
            file_path,
            line_number,
            content_hash,
            blame_commit,
            blame_parent,
            has_commit_link=(commit_link is not None),
            linked_trace_ids=linked_trace_ids,
        )
        if score > best_score:
            best_score = score
            best_trace = trace
            best_signals = trace_signals

    if best_trace is None or best_score <= 0:
        return _no_attribution()

    # --- Require some evidence that this trace is the right one ---
    # Allow attribution when we have: (1) line-range evidence, or
    # (2) commit_link + content_hash (content proven), or
    # (3) commit_link + revision_parent (trace was linked to this commit and
    #     was at parent revision — trace touched this file, we already filtered
    #     by file; many traces don't store range info).
    has_range_evidence = "range_match" in best_signals or "range_overlap" in best_signals
    has_strong_evidence = "commit_link" in best_signals and "content_hash" in best_signals
    has_commit_and_revision = "commit_link" in best_signals and "revision_parent" in best_signals
    if not (has_range_evidence or has_strong_evidence or has_commit_and_revision):
        return _no_attribution()

    # --- Determine tier from signals ---
    tier = _compute_tier(best_score, best_signals)
    if tier is None:
        # Only weak signals (e.g. timestamp alone) — no attribution
        return _no_attribution()

    confidence = _tier_to_confidence(tier)

    # --- Extract metadata from the winning trace (enrich from other candidates if needed) ---
    return _build_result(
        tier=tier,
        confidence=confidence,
        trace=best_trace,
        file_path=file_path,
        line_number=line_number,
        content_hash=content_hash,
        signals=best_signals,
        commit_link_match="commit_link" in best_signals,
        content_hash_match="content_hash" in best_signals,
        project_id=project_id,
        other_candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Candidate trace finder
# ---------------------------------------------------------------------------

def _trace_touches_file(trace: dict[str, Any], file_path: str) -> bool:
    """Return True if this trace's files array contains an entry for *file_path*.

    Critical: commit links associate a commit with traces that touched *any*
    changed file. When blaming file F, we must only consider traces that
    actually touch F — otherwise we attribute F's lines to e.g. a .gitignore
    trace from the same commit.

    Uses top-level "files" column first; falls back to trace_record["files"]
    in case the DB row has files only inside the full record.
    """
    files_data = trace.get("files")
    if isinstance(files_data, str):
        try:
            files_data = json.loads(files_data)
        except (json.JSONDecodeError, TypeError):
            files_data = []
    if not files_data or not isinstance(files_data, list):
        # Fallback: read from trace_record (full trace JSON)
        tr = trace.get("trace_record")
        if isinstance(tr, str):
            try:
                tr = json.loads(tr)
            except (json.JSONDecodeError, TypeError):
                tr = {}
        if isinstance(tr, dict):
            files_data = tr.get("files") or []
        else:
            files_data = []
    if not isinstance(files_data, list):
        return False
    return _find_matching_file(files_data, file_path) is not None


def _find_candidate_traces(
    project_id: str,
    file_path: str,
    blame_commit: str,
    blame_parent: str | None,
    blame_timestamp: str | None,
    linked_trace_ids: list[str],
) -> list[dict[str, Any]]:
    """Gather candidate traces using three search strategies.

    Query strategy (in priority order):
    1. If linked_trace_ids is non-empty, fetch those traces directly.
    2. Query traces where vcs.revision == blame_parent and file path matches.
    3. Fallback: query traces in a timestamp window with matching file path.

    All strategies are attempted and results are merged (deduplicated by
    trace_id). We then filter to only traces that actually touch *file_path*
    — commit links can include traces that touched other files in the same
    commit (e.g. .gitignore), and we must not attribute this file to those.
    """
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []

    def _add(traces: list[dict[str, Any]]) -> None:
        for t in traces:
            tid = t.get("trace_id") or ""
            if tid and tid not in seen:
                seen.add(tid)
                candidates.append(t)

    # Path A: From commit link (may include traces that touched other files only)
    if linked_trace_ids:
        _add(db.find_traces_by_ids(project_id, linked_trace_ids))

    # Path B: From parent revision match (no file filter in DB — we filter below
    # so path matching is lenient, e.g. trace "vite.config.js" vs "frontend/vite.config.js")
    if blame_parent:
        _add(db.find_traces_by_revision(project_id, blame_parent))

    # Path C: Timestamp window fallback (no file filter in DB — filter below for lenient path)
    if blame_timestamp and len(candidates) < 5:
        try:
            ts = datetime.fromisoformat(blame_timestamp)
            since = (ts - timedelta(hours=24)).isoformat()
            until = (ts + timedelta(hours=1)).isoformat()
            _add(db.find_traces_in_time_window(project_id, since, until))
        except (ValueError, TypeError):
            logger.debug("Could not parse blame_timestamp: %s", blame_timestamp)

    # Require that every candidate actually touches the blamed file
    candidates = [t for t in candidates if _trace_touches_file(t, file_path)]
    return candidates


# ---------------------------------------------------------------------------
# Scoring function
# ---------------------------------------------------------------------------

def _score_trace(
    trace: dict[str, Any],
    file_path: str,
    line_number: int,
    content_hash: str | None,
    blame_commit: str,
    blame_parent: str | None,
    has_commit_link: bool,
    linked_trace_ids: list[str],
) -> tuple[float, list[str]]:
    """Score how well a candidate trace matches the blamed line.

    Returns
    -------
    tuple[float, list[str]]
        (numeric score, list of signal names that fired)

    Signal weights:
      commit_link_match:  +40  (trace ID in commit link's trace_ids)
      content_hash_match: +30  (content hash matches)
      revision_parent:    +15  (trace revision == blame parent)
      revision_ancestor:  +8   (trace revision is an ancestor of blame commit)
      range_match:        +10  (line falls within trace's range)
      range_overlap:      +5   (line is near trace's range)
      timestamp_match:    +5   (trace timestamp in commit window)
    """

    score: float = 0.0
    signals: list[str] = []

    trace_id = trace.get("trace_id", "")
    trace_record = trace.get("trace_record")
    if isinstance(trace_record, str):
        try:
            trace_record = json.loads(trace_record)
        except (json.JSONDecodeError, TypeError):
            trace_record = {}
    if not isinstance(trace_record, dict):
        trace_record = {}

    # --- Commit link match ---
    if has_commit_link and trace_id in linked_trace_ids:
        score += WEIGHT_COMMIT_LINK
        signals.append("commit_link")

    # --- VCS revision match ---
    vcs = trace.get("vcs")
    if isinstance(vcs, str):
        try:
            vcs = json.loads(vcs)
        except (json.JSONDecodeError, TypeError):
            vcs = {}
    if not isinstance(vcs, dict):
        vcs = {}

    trace_revision = vcs.get("revision", "")
    if trace_revision and blame_parent:
        if trace_revision == blame_parent:
            score += WEIGHT_REVISION_PARENT
            signals.append("revision_parent")
        elif _is_prefix_match(trace_revision, blame_parent):
            # Short SHA prefix match (e.g. trace stored abbreviated SHA)
            score += WEIGHT_REVISION_PARENT
            signals.append("revision_parent")

    # --- File & line range match ---
    files_data = trace.get("files")
    if isinstance(files_data, str):
        try:
            files_data = json.loads(files_data)
        except (json.JSONDecodeError, TypeError):
            files_data = []
    if not isinstance(files_data, list) or not files_data:
        # Fallback: files may only be in trace_record (e.g. DB column empty)
        files_data = trace_record.get("files") if isinstance(trace_record, dict) else []
    if not isinstance(files_data, list):
        files_data = []

    matched_file = _find_matching_file(files_data, file_path)
    if matched_file:
        range_result = _check_range(matched_file, line_number)
        if range_result == "exact":
            score += WEIGHT_RANGE_MATCH
            signals.append("range_match")
        elif range_result == "overlap":
            score += WEIGHT_RANGE_OVERLAP
            signals.append("range_overlap")

        # --- Content hash match ---
        if content_hash:
            file_hash = _extract_content_hash(matched_file, line_number)
            if file_hash and _hashes_match(content_hash, file_hash):
                score += WEIGHT_CONTENT_HASH
                signals.append("content_hash")

    # --- Timestamp match ---
    # If the trace timestamp falls within a plausible window around the commit
    trace_ts = trace.get("trace_timestamp")
    if trace_ts and blame_parent:
        # Simple heuristic: trace happened before the commit is a positive signal
        if _timestamp_plausible(trace_ts, blame_commit, blame_parent):
            score += WEIGHT_TIMESTAMP
            signals.append("timestamp_match")

    return score, signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_attribution() -> AttributionResult:
    """Return a blank attribution result."""
    return AttributionResult(
        tier=None,
        confidence=0.0,
        trace_id=None,
        conversation_url=None,
        conversation_content=None,
        contributor_type=None,
        model_id=None,
        tool=None,
        matched_range=None,
        content_hash_match=False,
        commit_link_match=False,
        signals=[],
    )


def _build_result(
    *,
    tier: int | None,
    confidence: float,
    trace: dict[str, Any],
    file_path: str,
    line_number: int,
    content_hash: str | None,
    signals: list[str],
    commit_link_match: bool,
    content_hash_match: bool,
    project_id: str,
    other_candidates: list[dict[str, Any]] | None = None,
) -> AttributionResult:
    """Build a full AttributionResult from the winning trace.

    If model_id or conversation_url are missing, tries to fill from
    other_candidates (e.g. other commit-linked traces).
    """

    trace_record = trace.get("trace_record")
    if isinstance(trace_record, str):
        try:
            trace_record = json.loads(trace_record)
        except (json.JSONDecodeError, TypeError):
            trace_record = {}
    if not isinstance(trace_record, dict):
        trace_record = {}

    # Extract tool info
    tool_data = trace.get("tool")
    if isinstance(tool_data, str):
        try:
            tool_data = json.loads(tool_data)
        except (json.JSONDecodeError, TypeError):
            tool_data = None
    if not isinstance(tool_data, dict):
        tool_data = trace_record.get("tool")

    # Extract file entry & range (prefer full trace_record["files"] for model/conversation)
    files_data = trace.get("files")
    if isinstance(files_data, str):
        try:
            files_data = json.loads(files_data)
        except (json.JSONDecodeError, TypeError):
            files_data = []
    if not isinstance(files_data, list):
        files_data = []
    # Use trace_record["files"] when top-level files is empty — DB column may be minimal
    if not files_data and trace_record:
        files_data = trace_record.get("files") or []
    if not isinstance(files_data, list):
        files_data = []

    matched_file = _find_matching_file(files_data, file_path)
    matched_range = None
    if matched_file:
        matched_range = _get_best_range(matched_file, line_number)

    # Extract model_id and conversation info — don't break until we have BOTH
    model_id = None
    conversation_url = None
    contributor_type = "unknown"

    if matched_file:
        conversations = matched_file.get("conversations", [])
        for conv in conversations:
            if not isinstance(conv, dict):
                continue
            contributor = conv.get("contributor") or {}
            if contributor.get("type") and not contributor_type:
                contributor_type = contributor["type"]
            if contributor.get("model_id") and not model_id:
                model_id = contributor["model_id"]
            conv_url = conv.get("url")
            if conv_url and not conversation_url:
                conversation_url = conv_url
            # Only break when we have both
            if model_id and conversation_url:
                break

    # Fallback: search ALL file entries in this trace for model/conversation
    if not model_id or not conversation_url:
        for fe in files_data:
            if not isinstance(fe, dict) or fe is matched_file:
                continue
            for conv in fe.get("conversations", []):
                if not isinstance(conv, dict):
                    continue
                contributor = conv.get("contributor") or {}
                if contributor.get("model_id") and not model_id:
                    model_id = contributor["model_id"]
                if contributor.get("type") and contributor_type == "unknown":
                    contributor_type = contributor["type"]
                if conv.get("url") and not conversation_url:
                    conversation_url = conv["url"]
            if model_id and conversation_url:
                break

    # Enrich from other candidate traces if still missing
    if (not model_id or not conversation_url) and other_candidates:
        best_trace_id = trace.get("trace_id")
        for t in other_candidates:
            if t.get("trace_id") == best_trace_id:
                continue
            m, u, ct = _extract_meta_from_trace(t, file_path)
            if not model_id and m:
                model_id = m
            if not conversation_url and u:
                conversation_url = u
            if ct and contributor_type == "unknown":
                contributor_type = ct
            if model_id and conversation_url:
                break

    # Try to look up conversation content from the database
    conversation_content = None
    if conversation_url:
        try:
            conversation_content = db.get_conversation_content(
                project_id, conversation_url
            )
        except Exception:
            pass  # Non-critical — don't fail attribution over this

    return AttributionResult(
        tier=tier,
        confidence=confidence,
        trace_id=trace.get("trace_id"),
        conversation_url=conversation_url,
        conversation_content=conversation_content,
        contributor_type=contributor_type,
        model_id=model_id,
        tool=tool_data,
        matched_range=matched_range,
        content_hash_match=content_hash_match,
        commit_link_match=commit_link_match,
        signals=signals,
    )


def _extract_meta_from_trace(
    trace: dict[str, Any],
    file_path: str,
) -> tuple[str | None, str | None, str | None]:
    """Extract (model_id, conversation_url, contributor_type) from a trace.

    Searches all file entries; file_path is used only to prefer the matching
    file entry. Returns (None, None, None) for missing values.
    """
    model_id = None
    conversation_url = None
    contributor_type = None
    files_data = trace.get("files")
    if isinstance(files_data, str):
        try:
            files_data = json.loads(files_data)
        except (json.JSONDecodeError, TypeError):
            files_data = []
    if not isinstance(files_data, list):
        return (None, None, None)
    for fe in files_data:
        if not isinstance(fe, dict):
            continue
        for conv in fe.get("conversations", []):
            if not isinstance(conv, dict):
                continue
            contributor = conv.get("contributor") or {}
            if contributor.get("model_id") and not model_id:
                model_id = contributor["model_id"]
            if contributor.get("type") and not contributor_type:
                contributor_type = contributor["type"]
            if conv.get("url") and not conversation_url:
                conversation_url = conv["url"]
        if model_id and conversation_url:
            return (model_id, conversation_url, contributor_type)
    return (model_id, conversation_url, contributor_type)


def _find_matching_file(
    files_data: list[dict[str, Any]],
    file_path: str,
) -> dict[str, Any] | None:
    """Find the file entry in a trace's files array that matches *file_path*."""
    for f in files_data:
        if not isinstance(f, dict):
            continue
        trace_path = f.get("path", "")
        if trace_path == file_path:
            return f
        # Handle relative vs absolute path differences
        if trace_path.endswith(file_path) or file_path.endswith(trace_path):
            return f
    return None


def _check_range(
    file_entry: dict[str, Any],
    line_number: int,
) -> str | None:
    """Check whether *line_number* falls within the file entry's ranges.

    Returns "exact" if the line is inside a recorded range, "overlap" if
    it's within 5 lines of a range boundary, or None if no match.
    """
    OVERLAP_MARGIN = 5

    # Ranges can live at the file level or inside conversations
    ranges = _collect_ranges(file_entry)

    for start, end in ranges:
        if start <= line_number <= end:
            return "exact"
        if (start - OVERLAP_MARGIN) <= line_number <= (end + OVERLAP_MARGIN):
            return "overlap"

    return None


def _collect_ranges(file_entry: dict[str, Any]) -> list[tuple[int, int]]:
    """Collect all (start_line, end_line) ranges from a file entry."""
    ranges: list[tuple[int, int]] = []

    # Top-level range on the file entry
    if "start_line" in file_entry and "end_line" in file_entry:
        try:
            ranges.append((int(file_entry["start_line"]), int(file_entry["end_line"])))
        except (ValueError, TypeError):
            pass

    # Ranges inside conversations (including conv["ranges"][] — trace.py format)
    for conv in file_entry.get("conversations", []):
        if not isinstance(conv, dict):
            continue
        if "start_line" in conv and "end_line" in conv:
            try:
                ranges.append((int(conv["start_line"]), int(conv["end_line"])))
            except (ValueError, TypeError):
                pass
        for r in conv.get("ranges", []):
            if isinstance(r, dict) and "start_line" in r and "end_line" in r:
                try:
                    ranges.append((int(r["start_line"]), int(r["end_line"])))
                except (ValueError, TypeError):
                    pass

    # Ranges inside changes
    for change in file_entry.get("changes", []):
        if not isinstance(change, dict):
            continue
        if "start_line" in change and "end_line" in change:
            try:
                ranges.append((int(change["start_line"]), int(change["end_line"])))
            except (ValueError, TypeError):
                pass

    return ranges


def _get_best_range(
    file_entry: dict[str, Any],
    line_number: int,
) -> dict[str, int] | None:
    """Return the range from *file_entry* that best covers *line_number*."""
    ranges = _collect_ranges(file_entry)
    if not ranges:
        return None

    # Prefer exact containing range, then nearest range
    best: tuple[int, int] | None = None
    best_distance = float("inf")

    for start, end in ranges:
        if start <= line_number <= end:
            # Exact hit — prefer the tightest (smallest) range
            span = end - start
            if best is None or span < (best[1] - best[0]):
                best = (start, end)
                best_distance = 0
        else:
            dist = min(abs(line_number - start), abs(line_number - end))
            if dist < best_distance:
                best = (start, end)
                best_distance = dist

    if best is None:
        return None
    return {"start_line": best[0], "end_line": best[1]}


def _extract_content_hash(
    file_entry: dict[str, Any],
    line_number: int,
) -> str | None:
    """Extract the content hash from *file_entry* that covers *line_number*.

    Content hashes can appear:
    - At the file entry level: file_entry["content_hash"]
    - Inside conversation ranges: conv["ranges"][i]["content_hash"] (trace.py format)
    - Inside conversations: conv["content_hash"]
    - Inside changes: change["content_hash"]
    """
    # Check conversation ranges first (most specific — trace.py stores hashes here)
    for conv in file_entry.get("conversations", []):
        if not isinstance(conv, dict):
            continue
        for r in conv.get("ranges", []):
            if not isinstance(r, dict):
                continue
            ch = r.get("content_hash")
            if ch and _range_contains(r, line_number):
                return ch

    # Conversation-level and change-level content_hash
    for conv in file_entry.get("conversations", []):
        if not isinstance(conv, dict):
            continue
        ch = conv.get("content_hash")
        if ch and _range_contains(conv, line_number):
            return ch

    for change in file_entry.get("changes", []):
        if not isinstance(change, dict):
            continue
        ch = change.get("content_hash")
        if ch and _range_contains(change, line_number):
            return ch

    # Fall back to file-level hash
    return file_entry.get("content_hash")


def _range_contains(entry: dict[str, Any], line_number: int) -> bool:
    """Return True if *entry* has start_line/end_line and they contain *line_number*."""
    try:
        return int(entry["start_line"]) <= line_number <= int(entry["end_line"])
    except (KeyError, ValueError, TypeError):
        return True  # No range info → assume it covers the line


def _hashes_match(hash_a: str, hash_b: str) -> bool:
    """Compare two content hashes, handling different-length prefixes.

    Supports both old 8-char and new 16-char hashes by comparing on the
    shorter prefix length.  Strips optional "sha256:" prefix.
    """
    a = hash_a.removeprefix("sha256:").lower()
    b = hash_b.removeprefix("sha256:").lower()

    min_len = min(len(a), len(b))
    if min_len == 0:
        return False
    return a[:min_len] == b[:min_len]


def _is_prefix_match(sha_a: str, sha_b: str) -> bool:
    """Return True if one SHA is a prefix of the other (handles abbreviated SHAs)."""
    min_len = min(len(sha_a), len(sha_b))
    if min_len < 7:
        return False  # Too short to be meaningful
    return sha_a[:min_len] == sha_b[:min_len]


def _timestamp_plausible(
    trace_ts: Any,
    blame_commit: str,
    blame_parent: str | None,
) -> bool:
    """Heuristic: return True if the trace timestamp is plausibly in the
    right time window for the blamed commit.

    Since we don't have the commit dates in this context, we just check
    that the trace timestamp is a valid datetime (positive signal that
    the trace exists in a relevant timeframe).  The actual timestamp
    comparison happens at the candidate-finding stage.
    """
    if not trace_ts:
        return False

    # If it's already a datetime object (psycopg2 auto-parses TIMESTAMPTZ)
    if isinstance(trace_ts, datetime):
        return True

    # Try parsing ISO-8601 string
    try:
        datetime.fromisoformat(str(trace_ts))
        return True
    except (ValueError, TypeError):
        return False
