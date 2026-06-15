# TracesHub

**The hosted home for [`agent-trace`](https://github.com/ujjalsharma100/agent-trace-cli) — GitHub for your AI coding traces.**

`git` has GitHub. `agent-trace` has TracesHub. The open-source [`agent-trace`
CLI](https://github.com/ujjalsharma100/agent-trace-cli) captures deterministic,
line-level AI authorship locally and syncs it to any
[`agent-trace-service`](https://github.com/ujjalsharma100/agent-trace-service).
**TracesHub is the hosted control plane** that gives those traces a multi-tenant
home: sign in with GitHub, install the app, import a repo, get a scoped token,
`agent-trace remote add`, push — and browse AI-attribution and conversation
context in a web UI you can share with your team.

This is a **standalone product** with **no code dependency on Curio** (see
[`docs/00-POSITIONING-AND-PRINCIPLES.md`](docs/00-POSITIONING-AND-PRINCIPLES.md)).
A future, opt-in "Connect to TracesHub" integration from Curio is anticipated
but deliberately deferred; the two evolve independently until then.

## Status

🔴 **Greenfield.** This repository currently contains only the design and the
phased implementation plan. Nothing is built yet.

## Where to start

| Doc | What it is |
|-----|------------|
| [`docs/00-POSITIONING-AND-PRINCIPLES.md`](docs/00-POSITIONING-AND-PRINCIPLES.md) | Why TracesHub is its own product; the independence rule; the Curio relationship; future integration. |
| [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md) | Target system: hub API + OSS storage service + web + CLI; the signed gateway; data model; topology. |
| [`docs/02-IMPLEMENTATION-PLAN.md`](docs/02-IMPLEMENTATION-PLAN.md) | **The build plan** — phases, steps, exit criteria, from repo setup to public launch parity. |
| [`docs/03-CURIO-BLUEPRINT-MAP.md`](docs/03-CURIO-BLUEPRINT-MAP.md) | For each capability, where Curio already solved it — reuse the *lesson*, not the code. |
| [`docs/OSS-SUBTREE-GUIDELINES.md`](docs/OSS-SUBTREE-GUIDELINES.md) | How we vendor + upstream the `agent-trace-*` subtrees; the generic-vs-platform boundary. |

## The pillars TracesHub hosts

TracesHub is the hosted layer over two independent OSS projects (the "git" to
TracesHub's "GitHub"). Both are **vendored here as git subtrees** so we keep a
working copy, fix issues we hit while building the hosted product, and push the
generally-applicable fixes back upstream (see
[`docs/OSS-SUBTREE-GUIDELINES.md`](docs/OSS-SUBTREE-GUIDELINES.md)):

- **[`clis/agent-trace-cli`](https://github.com/ujjalsharma100/agent-trace-cli)** — the client. Captures traces via editor hooks, builds deterministic per-line ledgers at commit time, `push`/`pull`/`sync` to a remote. The hub does not *run* it (users `pip install` it); it's vendored for visibility and to upstream fixes. TracesHub just points the CLI's `remote` at the hosted gateway.
- **[`services/agent-trace-service`](https://github.com/ujjalsharma100/agent-trace-service)** — the storage engine. A multi-tenant, opaque JSON datastore (traces, ledgers, commit-links, conversations, summaries, blobs). TracesHub vendors, builds, and runs it behind the hub's gateway; it also remains independently self-hostable (and could be taken private later).

TracesHub itself is **new code**: the GitHub-identity / org / project / token /
gateway / web control plane (`api/` + `web/`) that turns the OSS engine into a
product. Keep platform-specific code in `api/`/`web/`/`infra/`, not in the subtrees.

## License

TBD before first public release.
