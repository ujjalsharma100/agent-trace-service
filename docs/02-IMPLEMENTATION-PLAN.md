# TracesHub — Implementation Plan

Build TracesHub from empty repo to **parity with the agent-trace functionality
that exists in Curio today**. Phases are sequential; each lists **Goal**,
**Steps**, and a hard **Exit criteria** gate. This plan is grounded in Curio's
*implemented* code (see [`03-CURIO-BLUEPRINT-MAP.md`](03-CURIO-BLUEPRINT-MAP.md)),
reusing the patterns as a blueprint while writing all hub code fresh.

> **Independence rule (applies to every phase):** no code, package, or template
> shared with the Curio repo. Vendoring the OSS `agent-trace-*` repos is allowed
> (they aren't Curio). See [`00-POSITIONING-AND-PRINCIPLES.md`](00-POSITIONING-AND-PRINCIPLES.md).

## Naming & conventions (fixed up front)

| Thing | Value |
|-------|-------|
| Web domain | `traceshub.com` |
| API domain | `api.traceshub.com` (`http://localhost:8000` dev) |
| Hub API package | `tracehub_api` (FastAPI) |
| Hub DB | `tracehub` |
| Storage engine (subtree) | `services/agent-trace-service` (vendored), DB `agent_trace`, port `5050` |
| CLI (subtree) | `clis/agent-trace-cli` (vendored for visibility + upstreaming; hub does not run it) |
| OSS subtree workflow | branch → `git subtree push` → PR on OSS repo → `subtree pull --squash` (see `docs/OSS-SUBTREE-GUIDELINES.md`) |
| GitHub App name / slug | `TracesHub` / `traceshub` |
| PAT format | `thub_pat_<12hex>_<secret64hex>`, argon2(secret) stored |
| PAT scopes | `traces:read`, `traces:write` |
| Gateway route | `/at/{owner_slug}/{project_slug}/{upstream:path}` |
| Gateway secret env | `AGENT_TRACE_GATEWAY_SECRET` |
| Gateway headers | `X-AgentTrace-{Org,User,Project,Signature}` (accept `X-Curio-*` alias) |
| Provisioning secret | `ADMIN_SECRET` (shared hub ↔ service) |

## Phase map

| # | Phase | Depends on |
|---|-------|-----------|
| 0 | Repo, GitHub App registration, dev stack | — |
| 1 | Hub API foundations | 0 |
| 2 | GitHub OAuth login + users + personal orgs | 1 |
| 3 | GitHub App install + org/member sync + repo listing | 2 |
| 4 | Projects: import, provisioning, links, effective access | 3 |
| 5 | Token system (`thub_pat`) + CLI device auth + remote credentials | 4 |
| 6 | Storage engine: vendor + neutralize + deploy | 1 |
| 7 | The signed gateway (`/at/…`) | 5, 6 |
| 8 | Enable traces + traces config + CLI end-to-end | 7 |
| 9 | Web app surfaces | 4–8 |
| 10 | Sharing & collaboration | 4, 9 |
| 11 | Webhooks (installation lifecycle + push signal) | 3 |
| 12 | Security hardening (RLS, audit, export, delete, CI gate) | all |
| 13 | Productization & launch (compose, deploy, GitHub Action, docs) | 12 |
| 14 | Parity exit verification (acceptance script) | all |

---

## Phase 0 — Repo, GitHub App, dev stack

**Goal:** an empty-but-bootable monorepo and the manual GitHub/cloud prerequisites.

### Steps
- **0.1** Create the repo layout: `api/` (FastAPI), `web/` (Vite SPA),
  `services/agent-trace-service/` + `clis/agent-trace-cli/` (subtree mount points),
  `infra/` (compose, postgres init, secrets/, `.env.example`), `docs/`. Add root
  `README`, `CLAUDE.md`, `.gitignore`, `LICENSE` (decide license), `SECURITY.md`.
- **0.2 (MANUAL)** Register the **TracesHub** GitHub App. Permissions (leaner than
  Curio — no PR/Issues write): **Repository contents: Read**, **Metadata: Read**,
  **Members (org): Read**; **user identification (OAuth) enabled**; webhook events
  **`installation`, `installation_repositories`, `push`**. Generate a client
  secret + private key (`.pem`). Set callback URL → `api.traceshub.com/api/v1/auth/callback`
  and a dev callback via tunnel.
- **0.3** `infra/.env.example` with every var: `DATABASE_URL`, `SESSION_SECRET`,
  `ADMIN_SECRET`, `AGENT_TRACE_GATEWAY_SECRET`, `AGENT_TRACE_SERVICE_URL`,
  `GITHUB_APP_ID/CLIENT_ID/CLIENT_SECRET/WEBHOOK_SECRET/PRIVATE_KEY_PATH/SLUG`,
  `TRACEHUB_API_BASE_URL`, `TRACEHUB_WEB_BASE_URL`, `AGENT_TRACE_CLI_DOCS_URL`.
- **0.4 (MANUAL)** Dev tunnel (e.g. `cloudflared`/`ngrok`) for OAuth callback +
  webhook delivery; record the URL in `.env`.
- **0.5** `infra/docker-compose.dev.yml`: `db` (postgres:16-alpine, multi-DB init
  for `tracehub` + `agent_trace`), `tracehub-api` (:8000), `agent-trace-service`
  (:5050), `tracehub-web`. Shared `ADMIN_SECRET` + `AGENT_TRACE_GATEWAY_SECRET`.
- **0.6** Vendor the OSS components as git subtrees + adopt the OSS-boundary
  governance. Add remotes and `git subtree add` for
  `services/agent-trace-service` (from the OSS repo `main`) and
  `clis/agent-trace-cli` (from its OSS repo `main`) — see
  [`infra/SUBTREES.md`](../infra/SUBTREES.md). Land the governance set:
  [`docs/OSS-SUBTREE-GUIDELINES.md`](OSS-SUBTREE-GUIDELINES.md),
  `infra/SUBTREES.md`, and the auto-loaded `.claude/rules/oss-subtree-boundaries.md`.
  These keep platform-specific code out of the subtrees and define the
  branch → `subtree push` → PR → `subtree pull` upstreaming flow.

### Exit criteria
- `docker compose -f infra/docker-compose.dev.yml up` brings up `db` + a hello
  `tracehub-api` + `agent-trace-service /health` green.
- GitHub App exists; `.pem` + secrets present in gitignored `infra/secrets/`;
  callback + webhook URLs resolve through the tunnel.
- Both subtrees are present at a pinned upstream `main`; a trial
  `git subtree push`/`pull` round-trips against an OSS feature branch; the
  governance docs + rule are in place.

---

## Phase 1 — Hub API foundations

**Goal:** the FastAPI skeleton with config, DB, migrations, health, logging.

### Steps
- **1.1** `tracehub_api` package: `main.py` (app + lifespan + CORS to web origin +
  request-id/access-log middleware + exception handlers), `config.py`
  (`pydantic-settings`, all Phase 0.3 vars typed, blank→None), `db.py`
  (engine/session factory), `errors.py`, `logging_json.py`.
- **1.2** SQLAlchemy 2 `Base` + Alembic; first migration creates the `tracehub`
  schema (Phase-by-phase tables can land in their own migrations).
- **1.3** `health` router: `GET /healthz` + `GET /api/v1/version` (build SHA +
  schema version). OpenAPI at `/docs` (toggle via env).

### Exit criteria
- `GET /healthz` and `/api/v1/version` return 200 with build metadata.
- `alembic upgrade head` builds an empty-but-correct schema; CI runs `ruff` +
  `mypy` (or chosen typer) + an empty pytest green.

---

## Phase 2 — GitHub OAuth login + users + personal orgs

**Goal:** a user can sign in with GitHub; we persist them and a personal org.

### Steps
- **2.1** `services/github_oauth.py`: authorize-URL builder + code→token exchange +
  `GET /user`. `auth` router: `GET /api/v1/auth/login` (+ `POST /github`) issues
  `state` (persist in `oauth_states`), redirects to GitHub; `GET /callback`
  validates `state`, exchanges code, upserts identity.
- **2.2** `services/identity.py`: upsert `users` by `github_user_id`; create a
  **personal org** (`is_personal=true`, slug = sanitized login) + owner
  membership on first login.
- **2.3** `security/session.py`: signed HttpOnly+Secure+SameSite=Lax session
  cookie; `GET /api/v1/auth/whoami`; `GET|POST /logout`.
- **2.4** `validateSearch`-safe `redirect_to` (same-origin only); stale-state sweep.
- **2.5** Web login stub: a `/login` page that hits `/auth/login` and a guard that
  redirects unauthenticated users.

### Exit criteria
- OAuth round-trip (mocked GitHub in tests) creates exactly one `users` row + one
  personal org + owner membership; second login is idempotent.
- `whoami` returns the user, orgs, and per-org default access; logout clears the
  cookie. Tests cover bad/expired `state` (no user created).

---

## Phase 3 — GitHub App install + org/member sync + repo listing

**Goal:** install the App on a GitHub org, mirror it to a TracesHub org, list
installable repos.

### Steps
- **3.1** Install entry → `https://github.com/apps/traceshub/installations/new`;
  handle the post-install return (`installation_id`, possible `setup_action`),
  including the "OAuth during install" interleave.
- **3.2** `services/github_app.py`: App JWT (RS256 from `.pem`) → installation
  access tokens (cached, ≤1h). Helpers for repo + member calls.
- **3.3** Member sync: on install, create/update the GitHub-backed `orgs` row
  (`github_org_id`, `slug`), upsert `memberships` from org membership (role
  mapping), set `source='github'`.
- **3.4** Repo listing: `GET /api/v1/installations/{id}/repos` via the
  installation token (respect `repo_selection`).
- **3.5** Multiple installs + lazy re-sync; `github_installations` row per install.
- **3.6** Web: install button, `/setup` return handler, repo picker stub.

### Exit criteria
- Installing on an org creates the mirrored `orgs` row + `github_installations` +
  memberships; uninstall path handled (Phase 11 webhook completes lifecycle).
- Repo list returns the installation's repos; a non-member can't see the org.
  Tests mock all GitHub calls (no live network in CI).

---

## Phase 4 — Projects: import, provisioning, links, effective access

**Goal:** import a GitHub repo as a TracesHub project, provisioned end-to-end.

### Steps
- **4.1** `POST /api/v1/projects` (import): from `(installation, repo)` create a
  `projects` row (`org_id`, `slug` from repo name, `github_repo_*`,
  `default_branch`, `code_scope='read'`). Slug must satisfy the storage-service
  grammar `^[a-z0-9][a-z0-9._-]{0,63}$`.
- **4.2** `services/provisioning.py`: `ensure_pillar_org` (mirror the hub org into
  `agent-trace-service` via `POST /orgs` with the **same UUID**, `X-Admin-Secret`)
  + `register_project_in_agent_trace_service` (`POST /projects`, idempotent on 409
  `project_exists`). Skip HTTP cleanly when `ADMIN_SECRET` unset (API-only dev).
- **4.3** `project_links` row: set `agent_trace_project_id = project.slug` (and
  `github_repo_id`). This is the bridge the gateway later reads.
- **4.4** Project CRUD + `GET /api/v1/resolve` (slug→ids for the web), archive,
  delete (delete also de-provisions: `DELETE /projects/<id>` on the service).
- **4.5** `security/effective.py`: `effective_project_access(user, project)` →
  per-pillar `traces` level from org-membership default ∪ project-collaborator
  grant (rank none<read<write<admin). Single-pillar here, but keep the shape.

### Exit criteria
- Importing a repo creates the hub project, mirrors org+project into
  `agent-trace-service`, and writes the link — verified by a `GET /projects/<slug>`
  on the service returning 200.
- `effective(owner, project).traces == 'write'`; a non-member resolves to `None`.
- Delete removes the hub project and returns 204/404 from the service de-provision.

---

## Phase 5 — Token system + CLI device auth + remote credentials

**Goal:** users can mint scoped `thub_pat` tokens (web + CLI), and the CLI can log
in via device flow.

### Steps
- **5.1** `services/token_mint.py`: generate `thub_pat_<seg>_<secret>`, store
  argon2(secret) + `prefix`; **cap** requested scopes by the minter's effective
  access (`max_mintable_scopes_*`); user-scoped vs project-scoped; default 1y TTL,
  2y max. `POST /api/v1/tokens` (user or `?project=`).
- **5.2** `GET /api/v1/tokens` (list, metadata only), `DELETE /tokens/<id>`
  (revoke). Plaintext returned exactly once at mint.
- **5.3** `services/token_introspect.py` + `security/api_auth.py`: resolve a bearer
  PAT → `(user, org, project_scope, effective_access)`; reject revoked/expired;
  rate-limit introspection. This is what the gateway calls.
- **5.4** CLI device login: `POST /api/v1/auth/device` (hub-mediated RFC 8628
  device_code) + `POST /auth/device/poll` → session;
  `POST /api/v1/auth/cli/trace-remote-credentials` mints a project-scoped
  `traces:write` token + returns the ready remote URL in one call.
- **5.5** Web Settings → API tokens UI (create/copy-once/list/revoke) — wired
  fully in Phase 9, API-complete here.

### Exit criteria
- A user mints a project token scoped `traces:write`; minting a scope above the
  user's grant is rejected; revoked tokens fail introspection.
- Device flow yields a session in tests (mocked GitHub); `trace-remote-credentials`
  returns a usable token + URL.

---

## Phase 6 — Storage engine: build, neutralize, deploy

**Goal:** the vendored `agent-trace-service` subtree (added in Phase 0.6) runs as
TracesHub's storage backend.

### Steps
- **6.1** Build the subtree's Dockerfile into the compose stack; `init_db.py create
  && migrate` on boot; `/health` green against the `agent_trace` DB.
- **6.2** Configure gateway mode: set `AGENT_TRACE_GATEWAY_SECRET`; confirm
  `gateway_auth.try_resolve_gateway` accepts signed requests and falls back to
  bearer otherwise.
- **6.3 (generic OSS change → upstream via §0)** Generalize header/secret naming in
  the subtree: accept `X-AgentTrace-*` headers (keep `X-Curio-*` as a deprecated
  alias), document `AGENT_TRACE_GATEWAY_SECRET` as canonical. This is a strictly
  generic improvement — make it on a branch, `git subtree push` to an OSS feature
  branch, PR + merge on the OSS repo, `subtree pull` back (per
  [`OSS-SUBTREE-GUIDELINES.md`](OSS-SUBTREE-GUIDELINES.md)). *If deferred, TracesHub
  emits `X-Curio-*` and proceeds.* Keep any TracesHub-only behavior out of the
  subtree — it belongs in `api/`.
- **6.4** Confirm admin provisioning path (`POST /orgs`, `POST /projects` with
  `X-Admin-Secret`) used by Phase 4 works against the running service.

> Subtree hygiene (every phase that touches `services/agent-trace-service` or
> `clis/agent-trace-cli`): classify each change generic vs TracesHub-specific;
> generic fixes you discover while building go upstream via §0; platform glue stays
> in `api/`/`web/`/`infra/`.

### Exit criteria
- Service boots in compose, `/health` + `/api/v1/version` green.
- A signed gateway request (crafted in a unit test) verifies; an unsigned bearer
  request still authenticates. Admin org/project registration succeeds.

---

## Phase 7 — The signed gateway (`/at/…`)

**Goal:** the hub reverse-proxies CLI traffic to the storage service with full
auth, scoping, provisioning, limits, and idempotency. This is the core.

### Steps
- **7.1** `security/gateway.py`: HMAC signer — canonical
  `org_id\nuser_id\nproject_id\nsha256(body)`, emit `X-AgentTrace-*`.
- **7.2** `security/gateway_identity.py`: resolve caller (session **or** bearer
  PAT) → `GatewayIdentity(user, introspect)`.
- **7.3** `routers/trace_proxy.py` + `services/trace_gateway_proxy.py`: the
  `/at/{owner}/{project}/{upstream:path}` route implementing the 11-step pipeline
  from [`01-ARCHITECTURE.md §5`](01-ARCHITECTURE.md): resolve project → pin RLS org
  → `traces_enabled` gate → token-scope check → provisioning gate (link present) →
  upstream-path allowlist → rate/body limits → effective-access (read/write) →
  inject `project_id` into query+body → idempotency replay → sign + proxy →
  re-emit safe response headers.
- **7.4** `services/trace_gateway_paths.py`: allowlist of permitted upstream
  routes + which need `project_id` injected. `gateway_limits.py` (rate + body) +
  `gateway_idempotency.py` (TTL replay) + `gateway_provisioning.py`
  (`raise_unless_pillar_provisioned`).

### Exit criteria
- `agent-trace push`/`pull` against `/at/<org>/<repo>/...` round-trips through to
  the service and back (integration test with the real service container).
- A project-scoped token is 403'd on another project; a read-only token is 403'd on
  a write; disabled traces → 409 (write) / 404 (read); unprovisioned → 409;
  oversized body → 413; replayed idempotent POST returns the cached result.

---

## Phase 8 — Enable traces + traces config + CLI end-to-end

**Goal:** the toggle + the copy-paste setup that makes the CLI "just work."

### Steps
- **8.1** `services/trace_pillar_enable.py`: `POST /api/v1/projects/{id}/traces/enable`
  / `…/disable` — ensures org+project provisioned, sets
  `project_links.agent_trace_project_id`, flips `traces_enabled`, writes an audit
  row. Idempotent.
- **8.2** `services/trace_remote_config.py`: `GET /api/v1/projects/{id}/traces/config`
  returns the remote URL (`<api>/at/<org>/<repo>`), copy-paste CLI commands
  (`agent-trace remote add origin "<url>" --token <paste>`), and the
  `AGENT_TRACE_CLI_DOCS_URL`. Surface the same docs URL on `whoami`.
- **8.3** Live CLI e2e: drive the **vendored** `clis/agent-trace-cli` (editable
  install from the subtree, so fixes we make are testable + upstreamable). On a
  scratch repo: `agent-trace init` → record a fake hook session → commit →
  `remote add` (token from Settings or `trace-remote-credentials`) → `push` →
  `pull` on a clean clone → `blame` matches.

### Exit criteria
- Enabling traces provisions + links + sets `traces_enabled`; disabling keeps the
  link for re-enable.
- The documented copy-paste flow takes a fresh repo from zero to pushed traces
  visible via the gateway. The CLI e2e test passes against the compose stack.

---

## Phase 9 — Web app surfaces

**Goal:** the full trace-only web product (design mirrors Curio's SPA).

### Steps
- **9.0** SPA scaffold: Vite + React + TS + Tailwind + shadcn/ui + TanStack
  Router/Query; build pipeline + container.
- **9.1** App shell: top bar, org switcher, sidebar, auth guard (`whoami`
  `beforeLoad` redirect), authed 404 inside shell.
- **9.2** Design system + shared primitives + `/kitchen-sink` reference page.
- **9.3** Home/dashboard: org's projects + recent activity (last push, trace
  counts).
- **9.4** Import flow: `/new` + `/setup` — install App / pick installation / pick
  repo / create project.
- **9.5** Project overview `/p/$org/$repo`: enable-traces control, **CLI setup
  card** (from traces/config), metrics panel (trace/ledger/commit-link/conversation/
  summary counts), GitHub default-branch HEAD resolution.
- **9.6** **Trace files** `/p/$org/$repo/files`: path tree + per-line AI-attributed
  ranges from synced ledgers; when GitHub is linked with read scope, scope the list
  to the branch-tip commit; empty/loading/error states. (Render ledger JSON
  client-side; no blame logic server-side.)
- **9.7** Settings `/settings`: profile · **API tokens** (mint/copy-once/revoke) ·
  connected GitHub installations.
- **9.8** Cross-cutting: access-denied / not-enabled / empty-remote states; loading
  skeletons; error surfaces with retry.

### Exit criteria
- Authenticated walkthrough: login → import → enable → copy CLI setup → (push from
  CLI) → see metrics + trace files in the UI → mint/revoke a token in Settings.
- Read-only collaborators see read views and no enable/disable/token controls.

---

## Phase 10 — Sharing & collaboration

**Goal:** teams can share projects with per-pillar (traces) access levels.

### Steps
- **10.1** Org members API/UI: list, role, `default_traces_access`.
- **10.2** Project collaborators: add/update/remove with `traces_access`
  (read/write/admin); `POST/DELETE /api/v1/projects/{id}/collaborators`.
- **10.3** Invitations: preview/accept/decline/revoke via `invitations.token`;
  optional email (SMTP, off by default); `/invitations/$token` web page.
- **10.4** Enforcement everywhere: `effective()` consulted in the gateway + every
  read; PAT mint caps intersect collaborator grants.
- **10.5** Sharing UI `/p/$org/$repo/share`.

### Exit criteria
- A collaborator added with `traces_access=read` can browse but not push (gateway
  403 on write) and cannot mint a `traces:write` token.
- Invite → accept grants exactly the offered access; revoke/expire blocks accept.

---

## Phase 11 — Webhooks

**Goal:** keep installs and dashboards fresh from GitHub events.

### Steps
- **11.1** `POST /api/v1/webhooks/github` with HMAC signature verification
  (`GITHUB_APP_WEBHOOK_SECRET`).
- **11.2** `installation` / `installation_repositories`: create/suspend/delete
  installs + repo-selection changes → update `github_installations` (+ org sync).
- **11.3** `push`: resolve repo → project, update `project.last_push_at`, surface a
  "new traces likely — run `agent-trace push`" signal. (No network from hooks; this
  is GitHub→hub, informational.)
- **11.4** Idempotent + replay-safe delivery (dedupe on delivery id).

### Exit criteria
- Signed deliveries are processed; bad signatures 401. Install lifecycle reflects
  in `github_installations`; a push updates `last_push_at`. Tests replay captured
  payloads.

---

## Phase 12 — Security hardening

**Goal:** the Phase-12-equivalent posture Curio reached (RLS, audit, export,
delete, tenant-isolation CI gate).

### Steps
- **12.1** Postgres RLS on `tracehub`: non-superuser app role + policies keyed on a
  per-request `SET LOCAL app.current_org` GUC; gateway + single-org reads pin the
  org. Off-by-default in dev (superuser bypass), on in prod.
- **12.2** Audit log coverage: project create/delete, traces enable/disable, token
  mint/revoke, collaborator/invite changes — all write `audit_log` rows.
- **12.3** Data export: `GET /api/v1/orgs/{id}/export` → structured archive (traces
  JSONL + project/membership metadata) pulled via the gateway.
- **12.4** Right-to-delete: `DELETE` a project cascades hub rows + de-provisions the
  service project (cascade to traces/ledgers/conversations/blobs pointers).
- **12.5** Tenant-isolation CI gate: a test suite proving no cross-org/cross-project
  read or write is possible through the gateway or the service; runs on every PR.
- **12.6** Secret hygiene: redacted structured logs, `.env.example` documents every
  var, no plaintext tokens logged.

### Exit criteria
- With RLS role assumed, a query for org A returns zero org-B rows even if the
  WHERE clause is wrong. The tenant-isolation suite is green and **required** in CI.
- Export produces a complete archive; project delete leaves no orphaned trace data.

---

## Phase 13 — Productization & launch

**Goal:** ship `traceshub.com`.

### Steps
- **13.1** Self-host `docker-compose.yml` (db + api + service + web) + `DEV-SETUP`.
- **13.2** Hosted deploy: web (`traceshub.com`), api (`api.traceshub.com`), service
  (internal), managed Postgres, object storage for blobs (graduate off inline
  `bytea`), TLS/HSTS, secrets vault.
- **13.3** CLI distribution stays OSS: end users `pip install agent-trace-cli` /
  `curl | bash`. Releases are cut from the **OSS repo** (which receives the fixes we
  upstream from the `clis/agent-trace-cli` subtree via §0) — the vendored copy is
  our dev/test copy, not the distribution channel. Add a TracesHub quickstart to the
  CLI docs and a **GitHub Action** (`traceshub/agent-trace-action`) that installs the
  CLI and pushes traces in CI.
- **13.4** Public docs site: quickstart, "what we send," token/scopes, self-host,
  `SECURITY.md` disclosure policy.
- **13.5** Marketplace listing for the TracesHub GitHub App.

### Exit criteria
- A new user, from `traceshub.com`, completes GitHub login → install → import →
  enable → CLI push → web view with no operator intervention.
- Self-host compose reproduces the same flow on a clean machine.

---

## Phase 14 — Parity exit verification

**Goal:** prove TracesHub matches Curio's agent-trace functionality.

### The acceptance script (defines "done")
1. Fresh GitHub account → OAuth login → personal org created.
2. Install TracesHub App on a GitHub org → org mirrored, repos listed.
3. Import a repo → project provisioned, link written, service `GET /projects/<slug>` 200.
4. Enable traces → `traces_enabled` + link set.
5. Mint a project `traces:write` token (web) **and** via CLI device flow.
6. `agent-trace remote add` → `push` from a real recorded session → gateway round-trips.
7. Web overview shows metrics; **trace files** shows AI-attributed line ranges,
   scoped to the branch-tip commit.
8. Add a `read` collaborator → they browse but cannot push or mint write tokens.
9. Export the org archive; delete the project → no orphaned data; audit log
   reflects every step.
10. Tenant-isolation CI gate green and required.

### Exit criteria
- Every acceptance step passes on the hosted stack **and** self-host compose.
- Feature matrix vs Curio's trace surface shows no parity gap (and no
  decision/research scope creep).

---

## Cross-cutting checklists

**Per-PR security checklist** (from day one): every query filters by `org_id`
(and `project_id` where applicable); every endpoint declares required scope; no
customer data in logs; every new env var documented in `.env.example`; new
customer data has a retention class.

**Testing discipline:** mock all GitHub calls in CI (no live network); integration
tests run the real `agent-trace-service` container; the gateway and tenant-isolation
suites are required gates; a live CLI e2e (hooks→ledger→push→pull→blame) runs against
compose.

**Deferred (explicitly not parity):** decisions/ADR, research, cloud agents,
unified CLI, MCP, the Curio→TracesHub "connect" integration. Keep the public API
clean enough that the last one is easy later — but build none of it now.
