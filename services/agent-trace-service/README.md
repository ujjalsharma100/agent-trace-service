# `agent-trace-service` (subtree mount)

This directory is the **vendored mount point** for the open-source storage
engine [`agent-trace-service`](https://github.com/ujjalsharma100/agent-trace-service).

Code lands here via `git subtree` per
[`docs/02-IMPLEMENTATION-PLAN.md`](../../docs/02-IMPLEMENTATION-PLAN.md) (Phase
0.6) and [`infra/SUBTREES.md`](../../infra/SUBTREES.md). Until the subtree is
added, the tree is intentionally empty except for this note.

**Boundary:** keep TracesHub-hosted specifics in `api/`, `web/`, and `infra/`;
upstreamable changes belong in the OSS repo (see
[`docs/OSS-SUBTREE-GUIDELINES.md`](../../docs/OSS-SUBTREE-GUIDELINES.md)).
