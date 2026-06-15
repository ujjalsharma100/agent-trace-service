# Curio Blueprint Map — reuse the lesson, not the code

For each TracesHub capability, this points at where Curio **already implemented**
the same thing, so you can study the proven solution and rebuild it fresh. **Do
not import any of these files.** They live in a different repo and stay there.

> Paths are in the Curio repo (`curio-platform/`). They are reference reading
> material, not dependencies.

## Hub control plane (FastAPI) → `tracehub_api`

| TracesHub piece | Curio reference (study, then rewrite) | Notes for the rebuild |
|---|---|---|
| App entrypoint, middleware, router mounting | `api/curio_api/main.py` | Drop `decision_proxy`. Keep `trace_proxy`, `auth`, `installations`, `invitations`, `orgs`, `project_collaborators`, `projects`, `resolve`, `tokens`, `webhooks`, `health`. |
| Typed settings | `api/curio_api/config.py` | Rename `CURIO_*`→`TRACEHUB_*`/`AGENT_TRACE_*`. Drop decision/MCP/SMTP-for-proposals vars. Keep gateway/limits/RLS/retention/GitHub-app vars. |
| ORM models | `api/curio_api/models/schema.py` | Copy the *shape*; drop `DecisionPrLink`, all `decisions_*`, `decisions_enabled`. Note real quirks: `api_tokens.user_id` is **NOT NULL**; memberships carry `default_traces_access`; collaborators/invitations carry `traces_access`. |
| GitHub OAuth (web) | `api/curio_api/services/github_oauth.py`, `routers/auth.py` (`/login`,`/github`,`/callback`,`/logout`,`/whoami`) | Same flow; `oauth_states` table for CSRF. |
| GitHub device flow (CLI) | `api/curio_api/services/github_device_oauth.py`, `routers/auth.py` (`/device`,`/device/poll`,`/cli/trace-remote-credentials`) | RFC 8628, hub-mediated. `trace-remote-credentials` mints a project token + returns the remote URL in one call. |
| Identity upsert + personal org | `api/curio_api/services/identity.py` | User by `github_user_id`; personal org + owner membership on first login. |
| Session cookie | `api/curio_api/security/session.py` | HttpOnly+Secure+SameSite=Lax, rolling. |
| GitHub App (JWT → install tokens, repos, members) | `api/curio_api/services/github_app.py`, `install_sync.py`, `installation_repos.py`, `routers/installations.py` | RS256 from `.pem`; cache install tokens ≤1h; respect `repo_selection`. |
| Project import + CRUD + resolve | `api/curio_api/services/project_create.py`, `project_access.py`, `routers/projects.py`, `resolve.py` | Slug must match `^[a-z0-9][a-z0-9._-]{0,63}$` (storage-service grammar). |
| Provisioning into the service | `api/curio_api/services/provisioning.py`, `trace_pillar_enable.py` (`ensure_pillar_org`, `register_project_in_agent_trace_service`) | Same-UUID org mirror; idempotent on 409 `project_exists`; skip HTTP when `ADMIN_SECRET` unset. |
| Effective access | `api/curio_api/security/effective.py` | Per-pillar rank none<read<write<admin; org default ∪ collaborator grant. Single-pillar (`traces`) for TracesHub. |
| PAT minting (capped) | `api/curio_api/services/token_mint.py` | `thub_pat_<seg>_<secret>`, argon2(secret); `MINTABLE_SCOPES={traces:read,traces:write}`; cap by minter's effective access; 1y default / 2y max TTL. |
| PAT list/revoke/introspect | `api/curio_api/services/token_introspect.py`, `token_list_revoke.py`, `security/api_auth.py`, `routers/tokens.py` | Introspection rate-limited; revoked/expired rejected. |

## The gateway (the core) → `tracehub_api` `/at/…`

| TracesHub piece | Curio reference | Notes |
|---|---|---|
| Route | `api/curio_api/routers/trace_proxy.py` | `/at/{owner}/{project}/{upstream:path}`, all methods. |
| Pipeline | `api/curio_api/services/trace_gateway_proxy.py` | The 11-step `run_trace_at_gateway`: resolve→RLS pin→`traces_enabled`→token scope→provisioning→path allowlist→limits→effective access→inject `project_id`→idempotency→sign+proxy. **This is the file to internalize most carefully.** |
| Signer | `api/curio_api/security/gateway.py` | HMAC over `org_id\nuser_id\nproject_id\nsha256(body)`. Rename headers `X-Curio-*`→`X-AgentTrace-*`. |
| Caller identity | `api/curio_api/security/gateway_identity.py` | Session or PAT → `GatewayIdentity`. |
| Path allowlist + project-id injection | `api/curio_api/services/trace_gateway_paths.py` | Which upstream routes are allowed + which need `project_id`. |
| Limits / idempotency / provisioning gate | `api/curio_api/services/gateway_limits.py`, `gateway_idempotency.py`, `gateway_provisioning.py` | Rate (per PAT/user), max body bytes, TTL replay, `raise_unless_pillar_provisioned`. |
| RLS pin | `api/curio_api/security/rls.py` (`apply_request_org`) | `SET LOCAL app.current_org`. |
| Enable/disable + config | `api/curio_api/services/trace_pillar_enable.py`, `trace_remote_config.py` | Remote-URL parsing accepts `/at/<org>/<repo>` **and** `<org>/<repo>`. |

## Vendored OSS subtrees → not rebuilt, vendored (OSS, not Curio)

These are **not** in the blueprint-and-rebuild category — they are vendored as git
subtrees from their own public repos and improved upstream via the §0 workflow
([`OSS-SUBTREE-GUIDELINES.md`](OSS-SUBTREE-GUIDELINES.md)). Curio's copies are just a
convenient reference for *how they're wired into a gateway*.

- **`services/agent-trace-service`** — the storage engine. Key files: `app.py`
  (routes + `require_auth`/`require_admin`), `agent_trace_service.py` (token/sync
  orchestration), `database_service.py` (psycopg2), `gateway_auth.py` (HMAC verify —
  generalize headers here via an OSS PR), `sql/*.sql` (schema), `init_db.py`. Already
  multi-tenant with composite `(org_id, project_id)` FKs; no rework beyond
  gateway-name neutralization.
- **`clis/agent-trace-cli`** — the client (vendored for visibility + upstreaming;
  the hub doesn't run it). The hub only needs to match the HTTP contract its
  `remote add`/`push`/`pull` already speak.

Everything **below** this line (the hub `api/`, `web/`, `infra/`) is the
rebuild-from-blueprint code — written fresh, no Curio import.

## Web app → `web/`

| TracesHub piece | Curio reference | Notes |
|---|---|---|
| Router + auth guard | `web/src/router.tsx` | Drop `decisions*` routes. Keep `/`, `/new`, `/setup`, `/settings`, `/orgs/*`, `/p/$org/$repo`, `/p/$org/$repo/files`, `/p/$org/$repo/share`, `/invitations/$token`, `/kitchen-sink`. |
| Traces overview | `web/src/pages/project/TracesPage.tsx` | Metrics panel + GitHub-head resolution gating. |
| Trace files | `web/src/pages/project/TracesFilesPage.tsx`, `components/traces/*` (`TracesAlignedTraceFiles`, `TraceFileTree`, `FileBlame`, `TracesMetricsPanel`, `ConversationModal`) | Path tree + AI-attributed line ranges; branch-tip scoping; render ledger JSON client-side. |
| Queries | `web/src/queries/{traces,projects,tokens,auth,collaborators,invitations,installations}.ts` | Drop `decisions.ts`, `projectConfig.ts`'s decision bits. |
| Shell/primitives | `web/src/components/{AppShell,OrgSwitcher,UserMenu,layout,states}.tsx`, `components/ui/*` | shadcn/ui design system — rebuild, restyle to TracesHub brand. |

## Infra → `infra/`

| TracesHub piece | Curio reference | Notes |
|---|---|---|
| Dev compose | `infra/docker-compose.dev.yml` | Keep `db` (postgres:16, multi-DB init) + `tracehub-api` + `agent-trace-service` (:5050) + `tracehub-web`. **Drop** `decision-backend` + `decision-mcp`. Shared `ADMIN_SECRET` + `AGENT_TRACE_GATEWAY_SECRET`. |
| Postgres multi-DB init | `infra/postgres/init-multiple-dbs.sql` | Create `tracehub` + `agent_trace`. |
| Secrets | `infra/secrets/` (gitignored) | GitHub App `.pem`. |

## What to deliberately NOT carry over

`services/curio-decision-record/*`, `clis/` umbrella plans, all `decision_*` and
`research_*` code/columns, `decision_proxy`, the decision MCP, PR/Issues GitHub-App
write scopes, notifications-for-proposals, and every `*.md` planning doc about
decisions/research. TracesHub's GitHub App is **read-only** on repos.
