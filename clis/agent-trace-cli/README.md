# `agent-trace-cli` (subtree mount)

This directory is the **vendored mount point** for the open-source
[`agent-trace-cli`](https://github.com/ujjalsharma100/agent-trace-cli).

The hub does not execute this CLI; it is vendored for visibility, local
development, and upstreaming fixes. Code lands here via `git subtree` per
[`docs/02-IMPLEMENTATION-PLAN.md`](../../docs/02-IMPLEMENTATION-PLAN.md) (Phase
0.6) and [`infra/SUBTREES.md`](../../infra/SUBTREES.md).

**Boundary:** keep TracesHub-hosted specifics in `api/`, `web/`, and `infra/`;
upstreamable changes belong in the OSS repo (see
[`docs/OSS-SUBTREE-GUIDELINES.md`](../../docs/OSS-SUBTREE-GUIDELINES.md)).
