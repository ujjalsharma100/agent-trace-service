# agent-trace-service

A simple Flask application that collects and stores coding agent traces from tools like **Cursor** and **Claude Code**. Traces are sent to this centralised service and stored in PostgreSQL, enabling cross-project visibility, team collaboration, and persistent trace history.

This implementation is built to the [Agent Trace](https://agent-trace.dev/) specification. 

## Project Structure

```
agent-trace-service/
├── app.py                    # Flask endpoints (thin routing layer)
├── agent_trace_service.py    # Application / business logic
├── attribution.py            # AI blame / attribution engine (scoring, tiers)
├── database_service.py       # All database operations (psycopg2)
├── model.py                  # Dataclasses (Project, TraceFields, AttributionResult, etc.)
├── init_db.py                # CLI tool to create / drop / reset tables
├── sql/
│   ├── projects.sql          # Projects table DDL
│   ├── traces.sql            # Traces table DDL
│   ├── commit_links.sql      # Commit-to-trace links (for blame)
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

The service provides **AI attribution** for code: given a file and git-blame data (which commit introduced each line), it attributes lines to AI traces with a **confidence tier** (1–6). This powers the `agent-trace blame` command in the CLI (remote mode).

- **Commit links** — When the CLI’s post-commit hook runs, it records which traces were “active” for that commit. Those links are stored in `commit_links` and are the strongest signal for attribution.
- **Attribution engine** — `attribution.py` finds candidate traces (by commit link, revision match, or time window), scores them using weighted signals (commit link, content hash, revision, line range, timestamp), and maps the best match to a tier and confidence. Attribution is only returned when there is sufficient structural evidence (e.g. commit link + content hash, or range match).
- **Tiers** — Tier 1 is “provably certain” (commit link + content hash); tiers 2–6 represent decreasing confidence. See [ATTRIBUTION-ALGORITHM.md](ATTRIBUTION-ALGORITHM.md) for the full algorithm (signals, weights, gating, and service vs CLI behavior).

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

### Blame (AI attribution)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/blame` | Yes | Attribute file lines to AI traces. Client sends git-blame segment data; returns attributions with tier, confidence, trace_id, model_id, etc. |

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

### Conversation sync (no trace)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/conversations/sync` | Yes | Upsert conversation contents only. Used when the agent has finished a response (e.g. Cursor `afterAgentResponse`, Claude Code `Stop`). Does not create a trace. |

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
        "ranges": [{ "start_line": 1, "end_line": 10, "content_hash": "sha256:abcdef" }]
      }]
    }],
    "metadata": { "session_id": "sess-1", "conversation_id": "conv-1" }
  },
  "conversation_contents": [
    { "url": "file:///path/to/transcript.txt", "content": "...full transcript text..." }
  ]
}
```

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
    ├── attribution.py     ← Blame: candidate finding, scoring, tier mapping
    │
    ▼
database_service.py        ← All SQL queries (psycopg2)
    │
    ▼
model.py                   ← Dataclasses (Project, TraceFields, CommitLink, AttributionResult, etc.)
```

## License

Licensed under the [Apache License 2.0](LICENSE).
