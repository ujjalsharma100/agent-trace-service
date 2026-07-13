# agent-trace-service

A Flask **HTTP datastore** for AI coding-agent traces. It stores and returns **opaque JSON** (traces, ledgers, commit-links, conversation blobs). **All attribution, blame, and domain logic run in the [agent-trace CLI](https://github.com/ujjalsharma100/agent-trace-cli)** — this service does not score lines, infer AI authorship, or implement heuristics.

**See it in action:** [▶ 3-min demo (YouTube)](https://www.youtube.com/watch?v=J4LPhV9wURg) · [`agent-trace-cli`](https://github.com/ujjalsharma100/agent-trace-cli) (the client)

<p align="center">
  <img src="docs/assets/gif-3-local-viewer.gif" alt="Local file viewer with git + agent-trace blame — the self-host / local-first story" width="800">
</p>

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
| `AGENT_TRACE_GATEWAY_SECRET` | _(unset)_ | Enables the optional signed-gateway auth path. When unset, gateway mode is **off** and all requests use bearer tokens. See *Auth flow*. |
| `BLOB_MAX_BYTES` | `10485760` | Max body size accepted by `POST /api/v1/blobs`. |
| `BUILD_SHA` | `dev` | Surfaced via `/health` and `/api/v1/version`. Set in CI / Docker build. |
| `FLASK_DEBUG` | `0` | Set `1` for debug / auto-reload |

---

## Auth flow

Authentication paths, used for different jobs:

| Header | Used by | What it gates |
|--------|---------|---------------|
| `Authorization: Bearer at_…` | clients (CLI) | All sync routes, project list/get, `auth/whoami`. Scope is resolved per token. |
| `X-Admin-Secret: <secret>` | admin / bootstrap | Token mint/revoke, org create, project create (default-org path). Value must match `ADMIN_SECRET`. |
| `X-AgentTrace-{Org,User,Project,Signature}` | a control-plane gateway | Same routes as bearer, but the caller is a trusted reverse-proxy that has already authenticated the user and enforced scopes. **Optional, off by default** — only active when `AGENT_TRACE_GATEWAY_SECRET` is set. |

### Signed-gateway auth (optional)

A deployment can front this datastore with a control plane (e.g. a hosted
product) that authenticates users itself and reverse-proxies their CLI traffic.
Instead of forwarding a bearer token, the gateway signs each request with a
shared secret so the datastore can trust the asserted identity:

- **Canonical string** (newline-joined): `<org_id>\n<user_id>\n<project_id>\n<sha256-hex(body)>`
  (`project_id` is empty when absent; `body` is the raw request bytes).
- **Signature**: `HMAC-SHA256` of the canonical string keyed by
  `AGENT_TRACE_GATEWAY_SECRET`, hex-encoded, in `X-AgentTrace-Signature`.
- **Headers**: `X-AgentTrace-Org`, `X-AgentTrace-User`, `X-AgentTrace-Project`,
  `X-AgentTrace-Signature`. The legacy `X-Curio-*` names are accepted as a
  **deprecated alias** (canonical names win when both are present).

A presented-but-invalid signature is a hard `401` (it never silently falls back
to bearer). When `AGENT_TRACE_GATEWAY_SECRET` is unset the whole path is
disabled and requests use bearer tokens. Project registration / deletion still
requires the admin path. See `gateway_auth.py` and `tests/test_gateway_auth.py`.

A token is opaque (`at_<32 url-safe chars>`) and is scoped to a single `(org_id, project_id?)` tuple:

- **Org-scoped token** (`project_id_scope = NULL`) sees every project in that org. Required for `projects:write` if it carries that scope.
- **Project-scoped token** (`project_id_scope = <slug>`) is bound to one project — even a syntactically valid request against another slug returns `403/404` without leaking which projects exist.

Self-hosted single-tenant deployments are pre-seeded with the well-known **default org** (`00000000-0000-0000-0000-000000000001`); when you mint a token without specifying `org_id`, that's where it lands.

### 1. Mint an org-scoped token

```bash
curl -X POST http://localhost:5000/api/v1/tokens \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-laptop", "scopes": ["read", "write", "projects:write"]}'
# => {"id": "...", "token": "at_xxxx...", "prefix": "at_xxxx", ...}
```

The plaintext appears in the response **exactly once**. Store it in your secret manager and pass it to the CLI as `--token` (or `--token-env`).

### 2. (Optional) Register a non-default org

```bash
curl -X POST http://localhost:5000/api/v1/orgs \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"slug": "acme", "name": "Acme Corp"}'
```

Then mint tokens against that `org_id`:

```bash
curl -X POST http://localhost:5000/api/v1/tokens \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"org_id": "<ACME_UUID>", "name": "ci", "scopes": ["read","write","projects:write"]}'
```

### 3. Register a project

Project slugs are mandatory before sync routes accept traffic. The CLI does this for you on `remote add --create` or `project create`; under the hood it issues:

```bash
curl -X POST http://localhost:5000/api/v1/projects \
  -H "Authorization: Bearer $AT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "myrepo", "org_slug": "acme", "name": "My Repo"}'
```

The server cross-checks the `org_slug` in the body against the caller's org. The admin path (`X-Admin-Secret` instead of `Authorization`) writes under the default org unless the body specifies otherwise.

`201` on create, `409` on conflict (slug already registered), `400` on bad slug shape.

### 4. Verify scope before use

CLIs call `GET /api/v1/auth/whoami` before every push so a typo in the URL (`/acme-typo/myrepo`) fails loud instead of writing under the token's real org:

```bash
curl http://localhost:5000/api/v1/auth/whoami \
  -H "Authorization: Bearer $AT_TOKEN"
# => {"org_id":"...", "org_slug":"acme", "project_id_scope": null, "scopes":["read","write"]}
```

## API overview

Most routes require:

```http
Authorization: Bearer <token>
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
| `POST` | `/api/v1/tokens/verify` | No (body carries `token`) | Verify a presented token, return scope |
| `GET` | `/api/v1/auth/whoami` | Bearer | Resolved scope (`org_id`, `org_slug`, `project_id_scope`, `scopes`) for the presented token. Used by the CLI before every push/pull to refuse "wrong org" requests up front. |

### Orgs

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/orgs` | `X-Admin-Secret` | Create or fetch an org by slug. Returns `{id, slug, name}`. |

### Projects

Projects are explicit. The wire `project_id` is a slug matching `^[a-z0-9][a-z0-9._-]{0,63}$`, unique per org. CLIs bind to URLs of the form `<scheme>://<host>/<org_slug>/<project_id>`; the slug must be registered before sync routes accept traffic for it (otherwise they return `404 project_not_found`).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/projects` | `X-Admin-Secret` **or** org-scoped token with `projects:write` | Register a project under `(org_id, project_id)`. 201 on create, 409 on conflict, 400 on bad slug. |
| `GET` | `/api/v1/projects` | Bearer token | List projects in caller's org (project-scoped tokens see only their bound project). |
| `GET` | `/api/v1/projects/<project_id>` | Bearer token | Project metadata. 404 if missing or scope-blocked. |

The end-to-end flow (token mint → org → project register → scope verify) lives under [Auth flow](#auth-flow) above. The shortest path from the CLI is:

```bash
agent-trace project create https://traces.acme.com/acme/myrepo --token "$AT_TOKEN"
# or, bind a remote and register in one step:
agent-trace remote add origin https://traces.acme.com/acme/myrepo --token "$AT_TOKEN" --create
```

Project IDs are URL-encoded into the slug grammar `^[a-z0-9][a-z0-9._-]{0,63}$` — both client and server reject anything else.

### Sync (primary path for CLI `push` / `pull`)

Bulk upsert (**POST**, JSON body with `project_id` and `items`) and incremental pull (**GET**, `project_id`, `since`, `limit`).

| Method | Path | Description |
|--------|------|-------------|
| `POST` / `GET` | `/api/v1/sync/traces` | Traces |
| `POST` / `GET` | `/api/v1/sync/ledgers` | Attribution ledgers (opaque JSON from CLI) |
| `POST` / `GET` | `/api/v1/sync/commit-links` | Commit ↔ trace links |
| `POST` / `GET` | `/api/v1/sync/conversations` | Conversation pointers (inline body or `content_sha256` referencing an uploaded blob) |
| `POST` / `GET` | `/api/v1/sync/summaries` | Session-summary rows |

**GET** responses include `items` and `max_timestamp` for cursor-based sync. The client uses content-id manifests (not timestamps) as the source of truth — the cursor is a paging hint that the server reports back as `max(created_at)` from the prior page.

All sync routes are scoped to the caller's `(org_id, project_id)`. A request with `project_id` set to a slug that hasn't been registered returns `404 project_not_found`; the CLI surfaces this as a hint to run `agent-trace project create`.

### Direct fetch (read-only)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/traces/<trace_id>?project_id=` | Single trace JSON |
| `GET` | `/api/v1/ledgers/<commit_sha>?project_id=` | Ledger JSON for a commit (404 if missing) |
| `GET` | `/api/v1/conversations/<conversation_id>?project_id=` | Conversation pointer + inline content (or sha + size) |

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
7. `conversation_summaries.sql`
8. `commit_links.sql`

(Order is encoded in `init_db.SQL_FILES`; foreign-key targets always come before referencing tables.)

`create` is idempotent — each SQL file uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`. Running `migrate` against a fresh database does the same work; running it against an existing `002-multitenancy` database is a no-op.

### Schema version

The runtime schema version (advertised by `/health` and `/api/v1/version`) comes from `agent_trace_service.SCHEMA_VERSION`:

```json
{ "schema_version": "004-traces-created-at-idx", "build": "abcd1234", "name": "agent-trace-service" }
```

`init_db.SCHEMA_VERSION` is the tag the bootstrap script writes when creating tables (`005-conversation-id-and-summaries` today, reflecting the conversation-id + summaries DDL on disk). Bump **both** when `sql/` changes — a divergence between the two is a configuration drift bug.

The CLI's `agent-trace doctor` reads the runtime version and warns when the client expects a newer revision than the server advertises.

### Migrating from pre-multitenancy installs

There are **no pre-existing single-tenant deployments to migrate** in M0 — the service launched directly on the multi-tenant schema. The seeded **default org** (`00000000-0000-0000-0000-000000000001`) preserves the "no org needed" UX for self-hosted single-tenant operators: mint a token without `org_id` and it lands there; bind a remote with `http://host/default/<project>` and `project create` works without ever touching an org admin endpoint.

If a future schema break ships, the upgrade path will land here with explicit `python init_db.py migrate --to <revision>` semantics.

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
