# Subtree commands

TracesHub vendors two OSS components as git subtrees. Policy:
[`../docs/OSS-SUBTREE-GUIDELINES.md`](../docs/OSS-SUBTREE-GUIDELINES.md).

| Prefix | Upstream | Remote name (suggested) |
|--------|----------|-------------------------|
| `services/agent-trace-service` | https://github.com/ujjalsharma100/agent-trace-service | `oss-ats` |
| `clis/agent-trace-cli` | https://github.com/ujjalsharma100/agent-trace-cli | `oss-atc` |

## One-time remotes

```bash
git remote add oss-ats https://github.com/ujjalsharma100/agent-trace-service.git
git remote add oss-atc https://github.com/ujjalsharma100/agent-trace-cli.git
git fetch oss-ats && git fetch oss-atc
```

## Add (initial vendoring)

```bash
git subtree add --prefix services/agent-trace-service oss-ats main --squash
git subtree add --prefix clis/agent-trace-cli        oss-atc main --squash
```

## Pull upstream updates into this repo

```bash
git subtree pull --prefix services/agent-trace-service oss-ats main --squash
git subtree pull --prefix clis/agent-trace-cli        oss-atc main --squash
```

## Push a generic fix upstream (the §0 workflow)

Make the fix on a branch here, keeping it generic (no TracesHub-specific glue —
that belongs in `api/`/`web/`/`infra/`). Then push the subtree to an OSS **feature
branch**, open a PR on the OSS repo, review/merge to its `main`, and pull back:

```bash
# 1. push the subtree's history to an OSS feature branch
git subtree push --prefix services/agent-trace-service oss-ats fix/<short-name>

# 2. open a PR on github.com/ujjalsharma100/agent-trace-service, review, merge to main

# 3. bring the merged result back
git subtree pull --prefix services/agent-trace-service oss-ats main --squash
```

Same three steps for `clis/agent-trace-cli` via `oss-atc`.

## Notes

- `--squash` keeps this repo's history clean (one squashed commit per pull).
- Don't `subtree push … main` directly for large or platform-only stacks — go
  through a feature branch + PR (§0).
- If `agent-trace-service` is taken private later, repoint `oss-ats` at the private
  fork; the same commands apply.
