# TracesHub (monorepo)

Hosted product — **"GitHub for `agent-trace`"** (`traceshub.com`). New code:
**`api/`** (FastAPI hub), **`web/`** (React SPA), **`infra/`**. Plus **git subtree**
OSS components vendored from their public repos under **`services/agent-trace-service`**
and **`clis/agent-trace-cli`**.

Design & plan: [`docs/02-IMPLEMENTATION-PLAN.md`](./docs/02-IMPLEMENTATION-PLAN.md);
architecture: [`docs/01-ARCHITECTURE.md`](./docs/01-ARCHITECTURE.md).

## Independence from Curio (non-negotiable)

TracesHub shares **no code, package, or template** with the Curio repo. Curio's
trace implementation is a *blueprint to study and rebuild fresh*
([`docs/03-CURIO-BLUEPRINT-MAP.md`](./docs/03-CURIO-BLUEPRINT-MAP.md)), never an
import. Vendoring the `agent-trace-*` OSS repos is **not** a Curio dependency —
they are standalone open source. See
[`docs/00-POSITIONING-AND-PRINCIPLES.md`](./docs/00-POSITIONING-AND-PRINCIPLES.md).

## OSS subtrees — agent and human instructions

- **Policy (full):** [`docs/OSS-SUBTREE-GUIDELINES.md`](./docs/OSS-SUBTREE-GUIDELINES.md) — **§0** defines **upstream `main` + TracesHub-only** per subtree; generic fixes go **branch → subtree push → PR on the OSS repo → subtree pull**; hosted-only deltas stay here.
- **Subtree commands:** [`infra/SUBTREES.md`](./infra/SUBTREES.md).
- **Auto-loaded rule:** [`.claude/rules/oss-subtree-boundaries.md`](./.claude/rules/oss-subtree-boundaries.md).

Subtrees: [`agent-trace-service`](https://github.com/ujjalsharma100/agent-trace-service),
[`agent-trace-cli`](https://github.com/ujjalsharma100/agent-trace-cli).

**Rule of thumb:** keep platform-specific code (hosted gateway names, TracesHub
URLs, hosted-only headers/env) in `api/`/`web/`/`infra/`. Inside the subtrees,
prefer generic, upstreamable changes (bugfixes, tests, optional modes off by
default). Call out any unavoidable platform-specific change at the code boundary
and in the OSS PR.
