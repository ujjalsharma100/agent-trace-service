# TracesHub — Target Architecture

This mirrors Curio's **implemented** trace stack (verified against the live code,
not the planning docs) with decisions/research removed and everything renamed to
be product-neutral. It is a blueprint to **rebuild**, not to import.

## 1. Component map

```
┌─────────────────────────────────────────────────────────────────────┐
│                              USERS                                   │
│   Engineers · AI/platform teams · tech leads                        │
└───────────────┬───────────────────────────────┬─────────────────────┘
                │ HTTPS (web, cookie session)    │ HTTPS (CLI, bearer PAT)
                ▼                                 ▼
┌─────────────────────────┐         ┌──────────────────────────────────┐
│   TracesHub Web (SPA)   │  REST   │        TracesHub API (hub)        │
│   React+Vite+Tailwind   │◀───────▶│  FastAPI. NEW code.               │
│   +shadcn/ui            │ cookie  │  Auth · GitHub App · Orgs ·       │
└─────────────────────────┘         │  Projects · Tokens · Gateway ·    │
                                     │  Webhooks · RLS · Audit           │
   agent-trace CLI ───────bearer────▶│  /at/<org>/<repo>/api/v1/... ─┐   │
   (OSS subtree)                     └───────────────────────────────│───┘
                                                signed gateway HMAC   │
                                                                      ▼
                                          ┌────────────────────────────────┐
                                          │     agent-trace-service        │
                                          │  (OSS, vendored). Flask+        │
                                          │  psycopg2 + Postgres. Opaque    │
                                          │  JSON datastore. Also self-     │
                                          │  hostable standalone.           │
                                          └────────────────────────────────┘
        ┌──────────────────────────────┐
        │            GitHub            │  OAuth (user identity) + App
        │  (auth, repos, webhooks)     │  (install, repo read, members,
        └──────────────────────────────┘   push/installation webhooks)
```

Two databases: **`tracehub`** (hub control plane, SQLAlchemy 2 + Alembic) and
**`agent_trace`** (storage engine, raw SQL via `init_db.py`). The hub never
touches `agent_trace` directly — only over the signed gateway, exactly as Curio's
API never touches the agent-trace DB except through the proxy.

## 2. Request flows (the three the demo must prove)

### 2.1 Onboard + push from the CLI
```
Web: GitHub OAuth login → install TracesHub App on org → import repo
  → POST /projects (provision: ensure org+project in agent-trace-service, set link, traces_enabled)
  → mint thub_pat (project-scoped, traces:write) OR POST /auth/cli/trace-remote-credentials
CLI: agent-trace remote add origin https://traceshub.com/<org>/<repo> --token thub_pat_…
  → agent-trace push
     → hub /at/<org>/<repo>/api/v1/sync/* : introspect PAT → effective(traces) →
       provisioning gate → rate/body limits → idempotency → inject project_id →
       sign X-AT-* HMAC → proxy to agent-trace-service → 200
```

### 2.2 Browse in the web app
```
Web /p/<org>/<repo> → GET project (effective_access.traces) →
  trace metrics (counts) + GitHub default-branch HEAD (App installation token) →
  /p/<org>/<repo>/files → path tree + per-line AI-attributed ranges from synced ledgers
  (all reads go through the same /at/ gateway with traces:read).
```

### 2.3 Webhook keeps the dashboard fresh
```
GitHub push / installation events → POST /webhooks/github (HMAC verified) →
  installation lifecycle (create/suspend/delete) updates github_installations;
  push updates project.last_push_at / surfaces "new traces likely" signal.
```

## 3. Data model — TracesHub `tracehub` DB

Mirrors Curio's **implemented** `schema.py`, minus decision/research columns.
SQLAlchemy 2 ORM + Alembic migrations.

| Table | Key columns (trace-relevant) |
|-------|------------------------------|
| `users` | `id` UUID PK, `github_user_id` BIGINT UNIQUE, `github_login`, `email`, `avatar_url`, `last_seen_at` |
| `orgs` | `id` UUID PK, `github_org_id` BIGINT UNIQUE NULL, `slug` UNIQUE, `display_name`, `is_personal`, `plan` (`free` default) |
| `memberships` | `(user_id, org_id)` UNIQUE, `role` (owner/admin/member), `source`, **`default_traces_access`** (none/read/write/admin; default `write`) |
| `github_installations` | `installation_id` BIGINT UNIQUE, `org_id` FK NULL, `account_login`, `account_type`, `repo_selection` |
| `oauth_states` | `state` PK, `redirect_to`, `created_at` (CSRF for OAuth round-trip) |
| `projects` | `(org_id, slug)` UNIQUE, `github_repo_id`, `github_repo_full_name`, `default_branch`, `code_scope` (default `read`), **`traces_enabled`** (default false), `is_archived`, `last_push_at`, `created_by` |
| `project_links` | `project_id` PK FK, **`agent_trace_project_id`** TEXT (the storage-service slug), `github_repo_id` |
| `project_collaborators` | `(project_id, user_id)` UNIQUE, **`traces_access`** (default read), `invited_by` |
| `invitations` | `org_id`/`project_id` NULL, `github_login`/`email`, **`traces_access`**, `role`, `token` UNIQUE, `status` (pending), `expires_at` |
| `api_tokens` | `prefix`, `hash` (argon2), `user_id` **NOT NULL**, `org_id` NULL, `project_id` NULL, `scopes` TEXT[] (`traces:read`/`traces:write`), `expires_at`, `last_used_at`, `revoked_at` |
| `audit_log` | `org_id`/`project_id`/`actor_user_id`/`actor_token_id` NULL, `action`, `target_type`, `target_id`, `payload_jsonb` |

**Dropped vs Curio:** `decision_pr_links`, all `decisions_*` columns/scopes,
`decisions_enabled`, the decision project link. **Renamed:** wherever Curio says
`curio_*`, TracesHub says `tracehub_*` / `thub_*`.

## 4. Vendored OSS components (git subtrees)

Both OSS components are vendored as **git subtrees** so we keep working copies,
fix issues we hit while building the hosted product, and push generally-applicable
fixes upstream (a Docker image / PyPI package couldn't do that). Governance:
[`docs/OSS-SUBTREE-GUIDELINES.md`](OSS-SUBTREE-GUIDELINES.md) — generic changes go
**branch → `subtree push` → PR on OSS → `subtree pull`**; TracesHub-specific glue
stays in `api/`/`web/`/`infra/`.

- **`clis/agent-trace-cli`** — the client. Vendored for visibility + upstreaming;
  the hub does **not** run it. End users `pip install` it; e2e tests drive the
  vendored copy.
- **`services/agent-trace-service`** — the storage engine the hub runs (below).
  Could be taken **private** later; the subtree boundary keeps that option clean.

### 4.1 Storage engine — `services/agent-trace-service`

Run as-is (it's already multi-tenant and product-neutral in substance). Schema
(`sql/*.sql`): `orgs`, `tokens`, `projects` (`(org_id, project_id)` natural key),
`blobs` (content-addressed, SHA-256, inline `bytea` now / S3 `storage_url`
reserved), `traces`, `commit_links` (carries the per-commit `ledger` JSONB),
`conversation_contents` (inline or blob pointer), `conversation_summaries`.
Composite FKs `(org_id, project_id) → projects` block cross-org writes at the SQL
layer.

Endpoints (Flask): `/health`, `/api/v1/version`; admin `POST/GET /tokens`,
`DELETE /tokens/<id>`, `POST /orgs`, `POST /projects`; bearer `GET /projects`,
`GET/DELETE /projects/<id>`, `GET /auth/whoami`; sync `POST/GET
/sync/{traces,ledgers,commit-links,conversations,summaries}` (cursor-based:
`items` + `max_timestamp`); direct `GET /traces/<id>`, `/ledgers/<sha>`,
`/conversations/<id>`; blobs `HEAD/POST/GET /blobs/<sha256>`.

**One product-neutrality change** — a textbook generic, upstreamable fix (land it
via the §0 subtree workflow): generalize the gateway header/secret names. Today
`gateway_auth.py` reads `AGENT_TRACE_GATEWAY_SECRET` **or**
`CURIO_GATEWAY_SECRET_TRACE` and verifies `X-Curio-*` headers. TracesHub uses the
already-neutral `AGENT_TRACE_GATEWAY_SECRET` and emits product-neutral
`X-AgentTrace-*` headers; keep `X-Curio-*` accepted as a deprecated alias so the
change is non-breaking. Make it on a branch → `subtree push` → PR on the OSS repo →
`subtree pull`. (If deferred, TracesHub can simply emit the existing `X-Curio-*`
names — the gateway works either way. Prefer the generalization.)

## 5. The signed gateway (the heart of the hub)

A reverse proxy at `GET|POST|… /at/{owner_slug}/{project_slug}/{upstream:path}`
(mirrors Curio's `trace_proxy` + `trace_gateway_proxy`). Per request it:

1. **Resolves** `(owner_slug, project_slug)` → hub `Project` for the caller; 404 if not visible.
2. **Pins RLS** to the project's `org_id` (`SET LOCAL` GUC) as defence-in-depth.
3. **Checks `traces_enabled`** (409 on write / 404 on read if disabled).
4. **Checks token scope** (project-scoped PAT may only hit its own project).
5. **Checks provisioning** — the `project_links.agent_trace_project_id` must exist (writes 409 until provisioned).
6. **Allowlists the upstream path** (only known `/api/v1/...` trace routes).
7. **Enforces limits** — per-PAT/user rate limit, max body bytes.
8. **Resolves effective access** — `effective(user, project)` ∩ token claims; read needs `traces:read`, write needs `traces:write`.
9. **Injects `project_id`** (the storage slug) into query + JSON body for routes that need it.
10. **Idempotency** — replays a successful expensive POST for a TTL window keyed on `Idempotency-Key` + caller + path.
11. **Signs + proxies** — HMAC over `org_id\nuser_id\nproject_id\nsha256(body)`, emits `X-AgentTrace-{Org,User,Project,Signature}`, forwards to `AGENT_TRACE_SERVICE_URL`, streams the response back.

Public remote-URL shapes the CLI binds to (the OSS remote parser already accepts
both): `https://traceshub.com/<org>/<repo>` (GitHub-like short form) and
`https://api.traceshub.com/at/<org>/<repo>`.

## 6. Identity, auth, tokens

- **Web users:** GitHub OAuth (the App's user-identification flow). HttpOnly +
  Secure + SameSite=Lax session cookie, rolling. `oauth_states` table guards CSRF.
- **CLI users:** GitHub device flow (RFC 8628), hub-mediated
  (`POST /auth/device` → `device/poll`), exchanged for a session;
  `POST /auth/cli/trace-remote-credentials` mints a project-scoped remote token in
  one step.
- **PATs:** `thub_pat_<12hex>_<secret64hex>`; only argon2(secret) persisted +
  visible `prefix`. Scopes limited to `traces:read` / `traces:write`. Minting is
  **capped** by the minter's effective access (a user can't mint a token stronger
  than their own grant); project- and org-scoped variants.
- **Gateway → service:** HMAC signed headers (above). The service trusts verified
  headers; direct external/self-host callers use the service's own bearer tokens.

## 7. Web app surfaces (trace-only)

React + Vite + Tailwind + shadcn/ui (Curio's stack). TanStack Router + Query.
Routes:

- `/login`, `/invitations/$token`, `/kitchen-sink` (design ref) — public.
- `/` dashboard (recent activity, projects), `/new` + `/setup` (import flow),
  `/settings` (profile · **API tokens** · connected GitHub) — authed shell.
- `/orgs/$org`, `/orgs/$org/members`, `/orgs/$org/settings`.
- `/p/$org/$repo` overview (enable traces, CLI setup card, metrics),
  `/p/$org/$repo/files` (path tree + AI-attributed line ranges, GitHub-head
  scoped), `/p/$org/$repo/share` (collaborators + invites).

Design north star (kept from Curio): the project view answers *"how much of this
repo is AI-written, and why was each line written?"* at a glance.

## 8. Tech stack (matches Curio; chosen, not shared)

Python 3.11+ · FastAPI · SQLAlchemy 2 + Alembic · Postgres 16 · `httpx` ·
`pydantic-settings` · `argon2` (PAT hashing) · React + Vite + Tailwind + shadcn +
TanStack · Docker Compose for dev/self-host. Storage engine stays Flask +
psycopg2 (it's the vendored OSS service — don't rewrite it).

## 9. Deployment topology

- **Hosted:** `traceshub.com` (web) + `api.traceshub.com` (hub) + internal
  `agent-trace-service`; managed Postgres (two logical DBs); object storage (S3)
  for large transcripts when the blob backend graduates off inline `bytea`.
- **Self-host:** one `docker-compose.yml` — `db` (postgres:16), `tracehub-api`
  (:8000), `agent-trace-service` (:5050), `tracehub-web`. Mirrors Curio's dev
  compose minus decision-backend/decision-mcp. Shared `ADMIN_SECRET` (hub ↔
  service provisioning) and `AGENT_TRACE_GATEWAY_SECRET` (gateway HMAC).
