# OSS subtrees (`services/agent-trace-service`, `clis/agent-trace-cli`)

Full policy: **`docs/OSS-SUBTREE-GUIDELINES.md`** (§0 workflow). Commands:
**`infra/SUBTREES.md`**.

## Model

- This repo = **OSS upstream `main`** (per subtree) **+** **TracesHub-only** commits
  that may stay private until we generalize them or land them via an OSS PR.
- **Generic** subtree fixes/features: **branch** here → **`git subtree push`** to an
  OSS **feature branch** → **PR + review** on the OSS repo → merge OSS **`main`** →
  **`git subtree pull --squash`** here. Don't casually `subtree push … main` for big
  or platform-only stacks.

## Before editing or reviewing a subtree

1. **Classify** — Generic (useful to any agent-trace user/self-hoster) vs
   **TracesHub-specific** (our hosted gateway, URLs, hosted-only wire
   names/headers/env).
2. **Prefer generic** in subtrees — keep TracesHub glue in `api/`, `web/`, `infra/`.
3. **If platform-specific in a subtree is unavoidable** — **call it out first** (PR +
   code boundary): why, how it's env/config-gated, the OSS default.
4. **Upstream intent** — what must follow §0 vs what is monorepo-only.

## Independence from Curio

This repo shares **no code** with the Curio repo. Curio's trace implementation is a
*blueprint to rebuild*, never an import (`docs/03-CURIO-BLUEPRINT-MAP.md`). Vendoring
these OSS repos is **not** a Curio dependency.

## Commits

- Prefer **small, focused commits** for OSS-intended work; don't mix hosted glue and
  a generic fix in one subtree-touching commit.
- After OSS merges, note it in `docs/OSS-SUBTREE-GUIDELINES.md` §0-log.

## Quick examples

- **Avoid in subtrees (unless OSS PR):** hardcoded `traceshub.com` URLs; undocumented
  TracesHub-only headers/env; hosted billing/quota logic.
- **Good in subtrees:** bugfixes, tests, schema/endpoint fixes, the gateway header
  generalization (`X-AgentTrace-*` with `X-Curio-*` alias), optional modes off by
  default.
