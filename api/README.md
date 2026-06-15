# TracesHub hub API (`tracehub_api`)

FastAPI service for the hosted product (GitHub identity, orgs, projects, tokens,
gateway, webhooks). Package name and layout follow
[`docs/02-IMPLEMENTATION-PLAN.md`](../docs/02-IMPLEMENTATION-PLAN.md).

## Local run

```bash
cd api
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn tracehub_api.main:app --reload --port 8000
```

When running beside Curio, prefer port **8100** on the host (see naming table in
the implementation plan).

## Docker

The dev stack Dockerfile lands in Phase 0.5 (`infra/docker-compose.dev.yml`).
