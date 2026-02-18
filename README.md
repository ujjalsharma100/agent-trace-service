# agent-trace-service

A Flask application that collects and stores coding agent traces from tools like **Cursor** and **Claude Code**. Traces are sent to this centralised service and stored in PostgreSQL, enabling cross-project visibility, team collaboration, and persistent trace history.

This implementation is built to the [Agent Trace](https://agent-trace.dev/) specification.

For the **local file viewer** (browse files, git + agent-trace blame), see the [repo root README](../README.md#file-viewer-optional) or [agent-trace-viewer](../agent-trace-viewer/README.md).

## Project Structure

```
agent-trace-service/
├── app.py                    # Flask endpoints (thin routing layer)
├── agent_trace_service.py    # Application / business logic
├── attribution.py            # AI blame / attribution engine (ledger-first + heuristic scoring)
├── database_service.py       # All database operations (psycopg2)
├── model.py                  # Dataclasses (Project, TraceFields, AttributionResult, etc.)
├── init_db.py                # CLI tool to create / drop / reset tables
├── sql/
│   ├── projects.sql          # Projects table DDL
│   ├── traces.sql            # Traces table DDL
│   ├── commit_links.sql      # Commit-to-trace links + attribution ledger (JSONB)
│   └── conversation_contents.sql  # Conversation contents table DDL
├── ATTRIBUTION-ALGORITHM.md  # Detailed attribution algorithm documentation
├── requirements.txt
├── .env.example
└── .gitignore
```

## Prerequisites

- **Python 3.11+**
- **PostgreSQL 14+**

## Quick Start

### 1. Create a virtual environment

```bash
cd agent-trace-service
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env — set DB_HOST, DB_USER, DB_PASSWORD, AUTH_SECRET, etc.
```

### 4. Create the database

```bash
# Create the PostgreSQL database (if it doesn't exist)
createdb agent_trace

# Create all tables
python init_db.py create
```

### 5. Run the service

```bash
# Development (with auto-reload)
FLASK_DEBUG=1 python app.py

# Production
gunicorn app:app -b 0.0.0.0:5000
```

The service runs on `http://localhost:5000` by default.

## AI Blame / Attribution

The service provides **AI attribution** for code: given a file and git-blame data (which commit introduced each line), it attributes lines to AI traces. This powers the `agent-trace blame` command in the CLI (remote mode).

### Ledger-first attribution

The primary attribution mechanism is the **attribution ledger** — a deterministic per-line map built at commit time by the CLI's post-commit hook. The ledger records which lines were written by AI, by a human, or are mixed (AI-written then human-edited), based on per-line content hash matching against trace records.

When the CLI sends a commit link with a ledger attached, the service stores it as JSONB in the `commit_links` table. During blame, the service checks the ledger first:

- If a ledger exists for the commit and covers the file, attribution is returned directly with **confidence 1.0** (no heuristic needed).
- If no ledger exists, the service falls back to the heuristic scoring engine.

The ledger endpoint (`GET /api/v1/ledgers/<commit_sha>`) allows the CLI to fetch ledgers for remote-mode blame.

### Heuristic fallback

For commits that predate the ledger system, the service uses a weighted scoring engine:

- **Commit links** — When the CLI's post-commit hook runs, it records which traces were "active" for that commit. Those links are stored in `commit_links` and are the strongest heuristic signal.
- **Attribution engine** — `attribution.py` finds candidate traces (by commit link, revision match, or time window), scores them using weighted signals (commit link, content hash, revision, line range, timestamp), and maps the best match to a tier and confidence.
- **Tiers** — Tier 1 is "provably certain" (commit link + content hash); tiers 2–6 represent decreasing confidence. See [ATTRIBUTION-ALGORITHM.md](ATTRIBUTION-ALGORITHM.md) for the full algorithm (signals, weights, gating, and service vs CLI behavior).

## Database Management

`init_db.py` provides CLI commands for schema management:

```bash
python init_db.py create    # Create all tables
python init_db.py status    # Show row counts
python init_db.py drop      # Drop all tables (asks for confirmation)
python init_db.py reset     # Drop + recreate (asks for confirmation)

# Override the database URL
python init_db.py create --database-url postgresql://user:pass@host:5432/mydb
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_USER` | `postgres` | PostgreSQL user |
| `DB_PASSWORD` | `postgres` | PostgreSQL password |
| `DB_NAME` | `agent_trace` | PostgreSQL database name |
| `PORT` | `5000` | Server port |
| `AUTH_SECRET` | `dev-secret` | Secret for signing bearer tokens (change in production!) |
| `FLASK_DEBUG` | `0` | Set to `1` for Flask debug / auto-reload |

## API Reference

All protected endpoints require an `Authorization: Bearer <token>` header.

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Service + DB health check |

### Tokens

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/tokens/generate` | No | Generate a bearer token |
| `POST` | `/api/v1/tokens/verify` | No | Verify / decode a token |

#### Generate Token

```bash
curl -X POST http://localhost:5000/api/v1/tokens/generate \
  -H "Content-Type: application/json" \
  -d '{"user_id": "alice"}'
```

### Projects

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/projects` | Yes | Create or update a project |
| `GET` | `/api/v1/projects/<project_id>` | Yes | Get project info + stats |

### Traces

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/traces` | Yes | Ingest a single trace |
| `POST` | `/api/v1/traces/batch` | Yes | Ingest multiple traces |
| `GET` | `/api/v1/traces?project_id=X` | Yes | List traces (with filters) |
| `GET` | `/api/v1/traces/<trace_id>?project_id=X` | Yes | Get a single trace |

### Commit Links

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/commit-links` | Yes | Record a commit-to-trace link (optionally with attribution ledger) |
| `GET` | `/api/v1/commit-links/<commit_sha>?project_id=X` | Yes | Look up which traces contributed to a commit |

### Ledgers

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/ledgers/<commit_sha>?project_id=X` | Yes | Get the attribution ledger for a commit. Returns the deterministic per-line attribution map, or 404 if no ledger exists. |

**Response:**

```json
{
  "version": "1.0",
  "commit_sha": "abc123...",
  "parent_sha": "def456...",
  "committed_at": "2026-02-15T10:30:00Z",
  "created_at": "2026-02-15T10:30:01Z",
  "trace_ids": ["uuid-1", "uuid-2"],
  "files": {
    "src/utils/parser.ts": {
      "line_attributions": [
        {
          "start_line": 1,
          "end_line": 9,
          "type": "human"
        },
        {
          "start_line": 10,
          "end_line": 25,
          "type": "ai",
          "trace_id": "uuid-1",
          "model_id": "anthropic/claude-sonnet-4",
          "conversation_url": "file:///path/to/transcript.txt"
        },
        {
          "start_line": 26,
          "end_line": 30,
          "type": "mixed",
          "trace_id": "uuid-2"
        }
      ]
    }
  }
}
```

### Blame (AI attribution)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/blame` | Yes | Attribute file lines to AI traces. The service checks the ledger first for deterministic attribution; falls back to heuristic scoring for commits without a ledger. |

The client runs `git blame --porcelain` locally and sends one entry per blame segment (consecutive lines from the same commit). Each segment includes `start_line`, `end_line`, `commit_sha`, `parent_sha`, `content_hash`, and `timestamp`. The service returns merged attributions (adjacent segments with the same trace and tier are combined).

**Request body:**

```json
{
  "project_id": "my-project",
  "file_path": "src/utils/parser.ts",
  "blame_data": [
    {
      "start_line": 10,
      "end_line": 25,
      "commit_sha": "abc123...",
      "parent_sha": "def456...",
      "content_hash": "sha256:9f2e8a1b3c4d5e6f",
      "timestamp": "2026-02-10T14:30:00Z"
    }
  ]
}
```

**Response:** `{ "file_path": "...", "attributions": [ { "start_line", "end_line", "tier", "confidence", "trace_id", "model_id", "conversation_url", "signals", ... } ] }`

### Conversations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/conversations/sync` | Yes | Upsert conversation contents only. Used when the agent has finished a response (e.g. Cursor `afterAgentResponse`, Claude Code `Stop`). Does not create a trace. |
| `GET` | `/api/v1/conversations/content?project_id=X&url=Y` | Yes | Get full conversation content by URL (for viewer / blame UI). |

#### Query Parameters for `GET /api/v1/traces`

| Param | Description |
|-------|-------------|
| `project_id` | **(required)** Project identifier |
| `session_id` | Filter by session |
| `conversation_id` | Filter by conversation |
| `hook_event` | Filter by event type (`afterFileEdit`, `sessionStart`, etc.) |
| `tool_name` | Filter by tool (`cursor`, `claude-code`) |
| `model_id` | Filter by model |
| `since` | ISO timestamp — traces after this time |
| `until` | ISO timestamp — traces before this time |
| `limit` | Max results (default: 50, max: 200) |
| `offset` | Pagination offset |

#### Ingest Body (`POST /api/v1/traces`)

```json
{
  "project_id": "my-project",
  "trace": {
    "version": "1.0",
    "id": "uuid-here",
    "timestamp": "2026-02-11T15:00:00.000Z",
    "vcs": { "type": "git", "revision": "abc123" },
    "tool": { "name": "cursor", "version": "2.4.28" },
    "files": [{
      "path": "src/index.ts",
      "conversations": [{
        "url": "file:///path/to/transcript.txt",
        "contributor": { "type": "ai", "model_id": "anthropic/claude-sonnet-4" },
        "ranges": [{
          "start_line": 1,
          "end_line": 10,
          "content_hash": "sha256:abcdef",
          "line_hashes": [
            { "line_offset": 0, "hash": "sha256:1a2b3c4d5e6f7890" },
            { "line_offset": 1, "hash": "sha256:0987654321fedcba" }
          ]
        }]
      }]
    }],
    "metadata": {
      "session_id": "sess-1",
      "conversation_id": "conv-1",
      "edit_sequence": 3
    }
  },
  "conversation_contents": [
    { "url": "file:///path/to/transcript.txt", "content": "...full transcript text..." }
  ]
}
```

#### Commit Link Body (`POST /api/v1/commit-links`)

```json
{
  "project_id": "my-project",
  "commit_sha": "abc123...",
  "parent_sha": "def456...",
  "trace_ids": ["uuid-1", "uuid-2"],
  "committed_at": "2026-02-15T10:30:00Z",
  "ledger": {
    "version": "1.0",
    "commit_sha": "abc123...",
    "parent_sha": "def456...",
    "committed_at": "2026-02-15T10:30:00Z",
    "created_at": "2026-02-15T10:30:01Z",
    "trace_ids": ["uuid-1", "uuid-2"],
    "files": { "...": "..." }
  }
}
```

The `ledger` field is optional. When present, it is stored as JSONB and used for deterministic blame attribution.

#### Conversation sync body (`POST /api/v1/conversations/sync`)

Used by the CLI when the agent has finished a response (Cursor `afterAgentResponse`, Claude Code `Stop`). Only upserts conversation content; no trace is created.

```json
{
  "project_id": "my-project",
  "conversation_contents": [
    { "url": "file:///path/to/transcript.txt", "content": "...full conversation transcript..." }
  ]
}
```

## Architecture

```
app.py                     ← HTTP endpoints (Flask routes)
    │
    ▼
agent_trace_service.py     ← Business logic, token mgmt, trace/commit-link ingest, blame orchestration
    │
    ├── attribution.py     ← Blame: ledger-first attribution + heuristic scoring fallback
    │
    ▼
database_service.py        ← All SQL queries (psycopg2), ledger storage/retrieval
    │
    ▼
model.py                   ← Dataclasses (Project, TraceFields, CommitLink, AttributionResult, etc.)
```

## License

Licensed under the [Apache License 2.0](LICENSE).
