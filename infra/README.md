# TracesHub infrastructure

- **`postgres/init/`** — SQL run on first boot of the dev Postgres container
  (creates `tracehub` and `agent_trace` databases).
- **`.env.example`** — environment template; Phase 0.3 completes the variable
  list.
- **`docker-compose.db.yml`** — Postgres 16 for local dev (hub DB + `agent_trace`
  via init SQL). Phase 0.5 adds `docker-compose.dev.yml` with the full stack
  (API, storage service, web).

## Secrets (local only)

Create a directory **`infra/secrets/`** on your machine (it is gitignored).
Store GitHub App private keys (`.pem`), generated secrets, and other credentials
there — never commit them. See
[`docs/02-IMPLEMENTATION-PLAN.md`](../docs/02-IMPLEMENTATION-PLAN.md) (Phase 0
manual playbook).
