# TracesHub — OSS Subtree Guidelines

TracesHub vendors two open-source components as **git subtrees**:

- [`services/agent-trace-service`](https://github.com/ujjalsharma100/agent-trace-service) — the storage engine the hub runs.
- [`clis/agent-trace-cli`](https://github.com/ujjalsharma100/agent-trace-cli) — the client (vendored for visibility + to push fixes upstream; the hub does not run it).

We keep working copies here so that, as we build the hosted product, we can fix
issues we hit and **push the generally-applicable fixes back to OSS** — something a
pinned Docker image or PyPI package can't do. The cost is discipline: keep the
subtrees generic and keep TracesHub-specific glue out of them.

> This document is **not** copied from Curio. It is TracesHub's own governance,
> written for TracesHub's components. (The independence rule in
> [`00-POSITIONING-AND-PRINCIPLES.md`](./00-POSITIONING-AND-PRINCIPLES.md) is about
> not sharing code with the **Curio repo**; it does not constrain vendoring these
> independent OSS repos.)

## §0. The model

- **This repo = OSS upstream `main` (per subtree) + TracesHub-only commits** that
  may stay private until we choose to generalize them or land them via an OSS PR.
- **Generic fixes/features in a subtree:**
  **branch here → `git subtree push` to an OSS feature branch → PR + review on the
  OSS repo → merge OSS `main` → `git subtree pull --squash` back here.**
  Don't casually `subtree push … main` for large or platform-only stacks.
- **Hosted-only deltas** (things that only make sense for TracesHub) stay in
  `api/`/`web/`/`infra/` — **not** in the subtrees. If a platform-specific change
  inside a subtree is genuinely unavoidable, gate it behind env/config with an
  OSS-friendly default and call it out (see §3).

Commands for the push/pull dance live in [`../infra/SUBTREES.md`](../infra/SUBTREES.md).

## §1. Before editing a subtree — classify

1. **Generic** (useful to any `agent-trace` user / self-hoster) vs
   **TracesHub-specific** (our hosted gateway, our URLs, hosted-only wire
   names/headers/env).
2. **Prefer generic** inside subtrees. Put TracesHub glue in `api/`/`web/`/`infra/`.
3. **If platform-specific inside a subtree is unavoidable** — call it out first
   (in the PR and at the code boundary): *why*, how it's env/config-gated, and what
   the OSS default is.
4. **Decide upstream intent** — what must follow the §0 branch workflow vs what is
   monorepo-only.

## §2. Commit hygiene

- Prefer **small, focused commits** for work intended for OSS, so the
  `subtree push` produces a clean PR.
- Keep TracesHub-only commits separate from upstreamable ones — don't mix a hosted
  glue change and a generic bugfix in one commit that touches a subtree.
- After an OSS merge, note in §0 below what landed publicly (running log).

## §3. Quick examples

- **Avoid in subtrees (unless it's an OSS PR):** hardcoded `traceshub.com` URLs;
  undocumented TracesHub-only headers/env; hosted billing/quota logic.
- **Good in subtrees:** bugfixes, added tests, schema/endpoint fixes, the
  gateway header/secret **generalization** (e.g. accept `X-AgentTrace-*` with
  `X-Curio-*` as a deprecated alias — a strictly generic improvement), optional
  modes that default off.

## §4. The "go private later" option

If `agent-trace-service` is ever taken private (TracesHub-only), the subtree's
upstream simply points at the private repo and §0 still applies between this repo
and that fork. Keeping the generic/platform boundary clean now preserves the option
to (a) re-open it later or (b) maintain a clean generic core even while private.
The CLI is expected to stay public regardless (it's the client users install).

## §0-log — what has landed upstream

_(Append entries as generic fixes merge into the OSS repos. Empty at greenfield.)_

- _(none yet)_
