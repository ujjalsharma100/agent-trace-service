# TracesHub — Positioning & Principles

This is the load-bearing "why" document. Read it before the architecture or the
plan: it constrains both.

## 1. The one-line pitch

**TracesHub is GitHub for `agent-trace`.** Open-source `agent-trace` records
line-level AI authorship locally and syncs it like git. TracesHub is the hosted
remote + collaboration surface: GitHub login, install-the-app, import-a-repo,
scoped tokens, and a shareable web view of who (human or AI) wrote what, with the
conversation context behind it.

## 2. Why it is its own product (not a Curio pillar)

Curio's center of gravity is **decisions and research** — capturing and
assimilating knowledge fast. Agent traces *enhance* that, but the connection is
loose: tracing is a different **job** (observability / provenance of AI-written
code) for a different **buyer** (the AI engineer / platform team) against a
different **competitive set** (LangSmith, Langfuse, Phoenix, Braintrust, Weave).
Bundling it as Curio's tertiary pillar forces one product and one pitch to serve
two unrelated motions. Unbundled, each gets a legible, standalone-strong
position.

The split is also **architecturally natural**: in Curio today the trace stack
(`trace_proxy`, `trace_gateway_*`, `trace_pillar_enable`, the `agent-trace-*`
pillars) is a clean, parallel mirror of the decision stack with essentially no
cross-pillar coupling. Cutting along that line is surgery, not amputation.

## 3. The independence rule (non-negotiable)

**No shared code between the TracesHub repo and the Curio repo.** Not a shared
library, not a shared package, not a copy-at-init template.

Rationale (the user's, and correct): the moment two products share an artifact,
every change must be weighed against both consumers. You start writing
conditionals for needs only one side has and carrying backward-compatibility you
don't actually owe anyone. That coupling becomes a coordination tax and a
creativity bottleneck — one product can no longer be reasoned about
independently. For two products meant to **diverge**, duplicate maintenance is
the cheaper price.

What *is* shared and encouraged:

- **Knowledge, not code.** Curio already solved GitHub App auth, org/project
  modeling, `*_pat` token minting + scoping, RLS/tenant isolation, and the signed
  gateway proxy. Reuse those *patterns and lessons* as a proven blueprint (see
  [`03-CURIO-BLUEPRINT-MAP.md`](03-CURIO-BLUEPRINT-MAP.md)) — without importing a
  single line.
- **The OSS components.** `agent-trace-cli` and `agent-trace-service` are their own
  public repos, independent of Curio. TracesHub **vendors both as git subtrees** —
  not as a Curio dependency but as a dependency on standalone open source, exactly
  the GitHub↔git relationship. We keep working copies in-repo so we can fix issues
  we hit while building the hosted product and **push the generally-applicable
  fixes back upstream** (a Docker image / PyPI package couldn't do that). That
  carries an obligation: keep the subtrees generic and keep TracesHub-specific glue
  out of them — see [`OSS-SUBTREE-GUIDELINES.md`](OSS-SUBTREE-GUIDELINES.md).
  (Curio's de-bundling will *remove* those subtrees from Curio; the OSS repos live
  on as TracesHub's engine.)

> The independence rule is about the **Curio repo**. It does **not** constrain
> vendoring these OSS repos, and it does **not** conflict with TracesHub having its
> own OSS-boundary discipline (which is reused as a *pattern* from Curio, written
> fresh for TracesHub's components).

## 4. The Curio relationship — now and later

- **Now:** zero runtime or repo coupling. Develop, deploy, and version
  independently. Separate domains (`traceshub.com` vs Curio), separate GitHub
  Apps, separate databases, separate billing.
- **Later (deferred, opt-in):** a "Connect to TracesHub" integration *from*
  Curio — e.g. a Curio project links its repo to a TracesHub project and surfaces
  trace evidence next to decisions. This is a one-directional, configuration-level
  integration over public APIs; it is **not** in scope for reaching parity and
  must never reintroduce shared code. Design for it only to the extent of keeping
  TracesHub's public API clean and stable.

## 5. Primary-bet posture

Both TracesHub and Curio are **co-equal experiments** for now; focus follows the
market. The independence rule *supports* this: because the codebases don't share
anything, effort can pour into whichever side the market rewards without the other
acting as ballast.

## 6. Inherited non-negotiables (from `agent-trace`'s design)

These constraints come from the OSS pillars and must hold in TracesHub too:

1. **Hooks never call the network.** Capture writes to local disk only; sync to
   TracesHub is an explicit `push`/`pull`/`sync` (or webhook-driven from the hub
   side), never from an editor/git hook.
2. **The storage service stays opaque.** It stores and returns JSON; it never
   interprets ledgers or computes blame. All attribution logic lives in the CLI.
   TracesHub's web UI renders ledger JSON client-side; it does not move blame
   logic server-side.
3. **Standalone install stays trivial.** `pip install agent-trace-cli` and
   self-hosting `agent-trace-service` keep working with no TracesHub account. The
   `curl | bash` path stays. TracesHub is an *option*, not a requirement.
4. **CLI project identity stays path-based.** The CLI keys projects by sanitized
   filesystem path (`-Users-jane-myrepo`). TracesHub carries its own
   `(org, project)` identity and bridges to the path-based id via a link table —
   it never forces the CLI to change its identifiers.

## 7. What "parity" means for the first milestone

"Build TracesHub to the point of how much functionality is in Curio right now" =
replicate **everything in Curio that serves agent-trace**, and nothing that
doesn't:

**In scope (parity target):** GitHub OAuth login + sessions; GitHub App install +
org/member sync + repo listing; orgs (personal + GitHub-mirroring) + memberships;
project import + provisioning into the storage service + the project link;
`traces_enabled` toggle + traces config (remote URL + CLI setup + docs URL);
`*_pat` token mint/list/revoke/introspect + CLI device login + trace-remote
credentials; the signed `/at/<org>/<repo>/…` gateway with provisioning gate,
idempotency, rate/body limits, and effective-access enforcement; the multi-tenant
storage service (sync/blobs/whoami); the web app (dashboard, import, project
overview, trace metrics, **trace files** with ledger line evidence + GitHub-head
scoping, settings/API tokens, sharing & collaboration); webhooks (installation
lifecycle, push signal); and Phase-12-equivalent security (RLS, audit log, data
export, right-to-delete, tenant-isolation CI gate).

**Out of scope (Curio-only; never port):** decisions/ADR pillar, research agent,
cloud agents, the unified `curio` CLI, decision MCP, notifications-for-proposals.

See [`02-IMPLEMENTATION-PLAN.md`](02-IMPLEMENTATION-PLAN.md) for how this gets
built.
