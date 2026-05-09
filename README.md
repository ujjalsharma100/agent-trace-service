# agent-trace-service

A Flask **HTTP datastore** for AI coding-agent traces. It stores and returns **opaque JSON** (traces, ledgers, commit-links, conversation blobs). **All attribution, blame, and domain logic run in the [agent-trace CLI](../agent-trace-cli/)** — this service does not score lines, infer AI authorship, or implement heuristics.

This implementation follows the [Agent Trace](https://agent-trace.dev/) specification and the redesign in the umbrella workspace (same relative paths work when this repo sits next to `AGENT-TRACE-NEW-PROPOSAL.md` at the monorepo root):

- [AGENT-TRACE-NEW-PROPOSAL.md](../AGENT-TRACE-NEW-PROPOSAL.md) — local-first, deterministic-only blame in the CLI, git-like sync, optional remote as mirror
- [IMPLEMENTATION-PLAN.md](../IMPLEMENTATION-PLAN.md) — service simplification (Phase 7) and related phases

For the **local file viewer** (browse files, git + agent-trace blame), see the [monorepo README](../README.md#file-viewer-optional) or install instructions in [agent-trace-cli](../agent-trace-cli/README.md).

---

## Role in the architecture

| Layer | Responsibility |
|--------|------------------|
| **CLI + hooks** | Capture traces, build **deterministic ledgers** at commit time, `push` / `pull` / `sync`, git notes |
| **This service** | Authenticated bulk **upsert** and **incremental fetch** of stored JSON; single-record **GET** by id |

Hooks **never** call the network. The user runs **`agent-trace push`** / **`pull`** when they want to mirror data to PostgreSQL.

---

## Project structure

```
agent-trace-service/
├── app.py                    # Flask routes (thin HTTP layer)
├── agent_trace_service.py    # Token handling, sync orchestration
├── database_service.py       # PostgreSQL (psycopg2)
├── model.py                  # Dataclasses
├── init_db.py                # Create / drop / reset schema
├── sql/                      # Table DDL
├── schemas/                  # JSON schemas (shared with CLI in full workspace)
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Prerequisites

- **Python 3.11+**
- **PostgreSQL 14+**

---

## Quick start

### 1. Virtual environment

```bash
cd agent-trace-service
python -m venv .venv
source .venv/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Set DB_* variables, ADMIN_SECRET, etc.
```

### 4. Create the database

```bash
createdb agent_trace
python init_db.py create
```

### 5. Run the service

```bash
# Development
FLASK_DEBUG=1 python app.py

# Production
gunicorn app:app -b 0.0.0.0:5000
```

Default URL: `http://localhost:5000`.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_USER` | `postgres` | PostgreSQL user |
| `DB_PASSWORD` | `postgres` | PostgreSQL password |
| `DB_NAME` | `agent_trace` | Database name |
| `PORT` | `5000` | HTTP port |
| `ADMIN_SECRET` | `dev-admin-secret` | Required by `X-Admin-Secret` for token + org admin endpoints. Change in production. |
| `BLOB_MAX_BYTES` | `10485760` | Max body size accepted by `POST /api/v1/blobs`. |
| `BUILD_SHA` | `dev` | Surfaced via `/health` and `/api/v1/version`. Set in CI / Docker build. |
| `FLASK_DEBUG` | `0` | Set `1` for debug / auto-reload |

---

## API overview

Most routes require:

```http
Authorization: Bearer <token>
```

A token is opaque (`at_<32 url-safe chars>`) and is scoped to a single
`(org_id, project_id?)` tuple. Tokens are minted by the admin endpoint —
the admin must provide the `X-Admin-Secret` header matching `ADMIN_SECRET`.
Self-hosted single-tenant deployments are pre-seeded with the well-known
**default org** (`00000000-0000-0000-0000-000000000001`); if you don't
specify `org_id` when issuing a token, that's where it lands.

```bash
curl -X POST http://localhost:5000/api/v1/tokens \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-laptop"}'
# => {"id": "...", "token": "at_xxxx...", "prefix": "at_xxxx", ...}
```

### Discovery & health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | Service name, version, endpoint map |
| `GET` | `/health` | No | Health + DB connectivity + `schema_version` + `build` |
| `GET` | `/api/v1/version` | No | `{schema_version, build, name}` for client compat checks |

### Tokens

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/tokens` | `X-Admin-Secret` | Issue token (returns plaintext exactly once) |
| `GET` | `/api/v1/tokens` | `X-Admin-Secret` | List tokens (metadata only — no plaintext) |
| `DELETE` | `/api/v1/tokens/<id>` | `X-Admin-Secret` | Revoke a token |
| `POST` | `/api/v1/tokens/verify` | No | Verify a presented token, return scope |

### Orgs

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/orgs` | `X-Admin-Secret` | Create or fetch an org by slug |

### Sync (primary path for CLI `push` / `pull`)

Bulk upsert (**POST**, JSON body with `project_id` and `items`) and incremental pull (**GET**, `project_id`, `since`, `limit`).

| Method | Path | Description |
|--------|------|-------------|
| `POST` / `GET` | `/api/v1/sync/traces` | Traces |
| `POST` / `GET` | `/api/v1/sync/ledgers` | Attribution ledgers (opaque JSON from CLI) |
| `POST` / `GET` | `/api/v1/sync/commit-links` | Commit ↔ trace links |
| `POST` / `GET` | `/api/v1/sync/conversations` | Conversation content blobs |

**GET** responses include `items` and `max_timestamp` for cursor-based sync.

### Direct fetch (read-only)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/traces/<trace_id>?project_id=` | Single trace JSON |
| `GET` | `/api/v1/ledgers/<commit_sha>?project_id=` | Ledger JSON for a commit (404 if missing) |
| `GET` | `/api/v1/conversations/<url_hash>?project_id=` | Conversation pointer + inline content (or sha + size) |

Query-parameter names match `app.py` (see source for exact spelling).

### Conversation blobs (chunked upload)

Large transcripts go through a content-addressed blob path so the same
content is uploaded once even when many traces reference it.

| Method | Path | Description |
|--------|------|-------------|
| `HEAD` | `/api/v1/blobs/<sha256>` | `200` if present, `404` otherwise |
| `POST` | `/api/v1/blobs` | Raw body upload (max `BLOB_MAX_BYTES`); returns `{sha256, size}` |
| `GET` | `/api/v1/blobs/<sha256>` | Raw bytes (`application/octet-stream`) |

The CLI (`agent-trace push`) prefers the blob path for any transcript above
its inline threshold (256 KiB). Inline content via the conversations sync
endpoint is still accepted for small blobs.

---

## Database management

```bash
python init_db.py create    # Create tables (idempotent)
python init_db.py migrate   # Alias for create (no legacy data to migrate)
python init_db.py status    # Row counts
python init_db.py drop      # Drop all (confirmation)
python init_db.py reset     # Drop + recreate (confirmation)

python init_db.py create --database-url postgresql://user:pass@host:5432/dbname
```

Schema files live under `sql/`. For table-level detail, inspect `sql/*.sql` in this directory. Application order:

1. `orgs.sql`
2. `tokens.sql`
3. `projects.sql`
4. `blobs.sql`
5. `traces.sql`
6. `conversation_contents.sql`
7. `commit_links.sql`

The schema version (currently `002-multitenancy`) is exposed by `/health` and `/api/v1/version` — bump `init_db.SCHEMA_VERSION` and `agent_trace_service.SCHEMA_VERSION` together when sql/ changes.

---

## Attribution and ledgers

**Blame is not computed here.** The CLI builds ledgers at commit time from trace line hashes and committed file content. This service only **persists** ledger JSON and serves it back for sync and optional `GET`. Missing ledger → the CLI reports **UNKNOWN**, not a server-side guess.

---

## Architecture

```
app.py
  → agent_trace_service.py   (auth, sync handlers)
  → database_service.py      (SQL)
  → model.py
```

---

## License

Licensed under the [Apache License 2.0](LICENSE).
