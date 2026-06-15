from fastapi import FastAPI

app = FastAPI(
    title="TracesHub API",
    description="Hosted control plane for agent-trace (see docs/02-IMPLEMENTATION-PLAN.md).",
    version="0.0.0",
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
