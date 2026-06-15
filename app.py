#!/usr/bin/env python3
"""
agent-trace-service  —  Pure datastore for AI agent traces.

The service stores and retrieves opaque JSON blobs.  ALL domain logic
(attribution, blame, scoring, summaries) lives exclusively in the CLI.

Routes:
    POST   /api/v1/sync/traces
    POST   /api/v1/sync/ledgers
    POST   /api/v1/sync/commit-links
    POST   /api/v1/sync/conversations
    POST   /api/v1/sync/summaries

    GET    /api/v1/sync/traces?project_id=&since=&limit=
    GET    /api/v1/sync/ledgers?project_id=&since=&limit=
    GET    /api/v1/sync/commit-links?project_id=&since=&limit=
    GET    /api/v1/sync/conversations?project_id=&since=&limit=
    GET    /api/v1/sync/summaries?project_id=&since=&limit=

    GET    /api/v1/traces/<id>?project_id=
    GET    /api/v1/ledgers/<commit_sha>?project_id=
    GET    /api/v1/conversations/<conversation_id>?project_id=

    HEAD   /api/v1/blobs/<sha256>
    POST   /api/v1/blobs                       (raw body)
    GET    /api/v1/blobs/<sha256>

    POST   /api/v1/tokens                      (admin: issue)
    GET    /api/v1/tokens                      (admin: list)
    DELETE /api/v1/tokens/<id>                 (admin: revoke)
    POST   /api/v1/tokens/verify               (verify a presented token)

    GET    /api/v1/auth/whoami                 (resolved scope for the bearer)

    POST   /api/v1/orgs                        (admin: register an org)

    POST   /api/v1/projects                    (admin or org-scoped + projects:write)
    GET    /api/v1/projects                    (list projects in caller's org)
    GET    /api/v1/projects/<project_id>       (project metadata)

    GET    /api/v1/version
    GET    /health

Run:
    python app.py                         (dev)
    gunicorn app:app -b 0.0.0.0:5000      (production)
"""

import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, request

import agent_trace_service as service
import database_service as db_service

load_dotenv()

PORT = int(os.environ.get("PORT", "5000"))

app = Flask(__name__)
app.teardown_appcontext(db_service.close_db)

# Match service.BLOB_MAX_BYTES + a small headroom so JSON sync routes still work.
app.config["MAX_CONTENT_LENGTH"] = service.BLOB_MAX_BYTES + 1 * 1024 * 1024


# ---------------------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------------------

def require_auth(f):
    """Resolve a Bearer token to (org_id, project scope, scopes) on g."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        ctx = service.resolve_token(auth[7:])
        if ctx is None:
            return jsonify({"error": "Invalid or revoked token"}), 401
        g.token_ctx = ctx
        g.org_id = ctx.org_id
        g.project_id_scope = ctx.project_id_scope
        g.scopes = ctx.scopes
        # ``user_id`` is retained as the token id so existing audit columns
        # downstream (``traces.user_id`` etc.) keep an actor reference.
        g.user_id = ctx.token_id
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    """Require X-Admin-Secret to match ADMIN_SECRET. Used by token admin routes."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-Admin-Secret", "")
        if not provided or provided != service.ADMIN_SECRET:
            return jsonify({"error": "Admin secret required"}), 403
        return f(*args, **kwargs)
    return wrapper


def _project_id_from_request() -> tuple[str | None, tuple[Response, int] | None]:
    """Resolve the request's project_id, check the token scope, and verify
    the project is registered.

    Returns ``(project_id, None)`` on success or ``(None, error_response)``.
    Sync routes are no longer permitted to lazily create projects — clients
    must register via ``POST /api/v1/projects`` first.
    """
    project_id = request.args.get("project_id")
    if project_id is None and request.method in ("POST",):
        body = request.get_json(silent=True) or {}
        project_id = body.get("project_id")
    if not project_id:
        return None, (jsonify({"error": "project_id is required"}), 400)
    if not service.assert_project_in_token_scope(g.token_ctx, project_id):
        return None, (jsonify({"error": "Token is not scoped to this project"}), 403)
    try:
        db_service.assert_project_exists(g.org_id, project_id)
    except db_service.ProjectNotFoundError:
        return None, (jsonify({
            "error": (
                f"Project {project_id!r} is not registered. "
                "Call POST /api/v1/projects (or `agent-trace project create <url>`) first."
            ),
            "code": "project_not_found",
        }), 404)
    return project_id, None


# ===================================================================
# Root / Health / Version
# ===================================================================

@app.route("/")
def root():
    return jsonify({
        "name": "agent-trace-service",
        "version": "1.1.0",
        "schema_version": service.SCHEMA_VERSION,
        "description": "Pure datastore — all domain logic lives in the CLI.",
        "endpoints": {
            "health": "GET /health",
            "version": "GET /api/v1/version",
            "sync_traces": "POST/GET /api/v1/sync/traces",
            "sync_ledgers": "POST/GET /api/v1/sync/ledgers",
            "sync_commit_links": "POST/GET /api/v1/sync/commit-links",
            "sync_conversations": "POST/GET /api/v1/sync/conversations",
            "sync_summaries": "POST/GET /api/v1/sync/summaries",
            "get_trace": "GET /api/v1/traces/<id>?project_id=",
            "get_ledger": "GET /api/v1/ledgers/<commit_sha>?project_id=",
            "get_conversation": "GET /api/v1/conversations/<conversation_id>?project_id=",
            "blob_head": "HEAD /api/v1/blobs/<sha256>",
            "blob_post": "POST /api/v1/blobs",
            "blob_get": "GET /api/v1/blobs/<sha256>",
            "tokens_admin": "POST/GET /api/v1/tokens, DELETE /api/v1/tokens/<id> (X-Admin-Secret)",
            "tokens_verify": "POST /api/v1/tokens/verify",
            "auth_whoami": "GET /api/v1/auth/whoami",
            "orgs_create": "POST /api/v1/orgs (X-Admin-Secret)",
            "projects_create": "POST /api/v1/projects (org-scoped + projects:write, or X-Admin-Secret)",
            "projects_list": "GET /api/v1/projects",
            "projects_get": "GET /api/v1/projects/<project_id>",
        },
    })


@app.route("/health")
def health():
    try:
        result = service.health_check()
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "db": "disconnected", "error": str(e)}), 503


@app.route("/api/v1/version")
def version():
    return jsonify(service.version_info())


# ===================================================================
# Tokens
# ===================================================================

@app.route("/api/v1/tokens", methods=["POST"])
@require_admin
def tokens_issue():
    body = request.get_json(silent=True) or {}
    org_id = body.get("org_id") or db_service.DEFAULT_ORG_ID
    project_id = body.get("project_id")  # None = org-scoped
    scopes = body.get("scopes")
    name = body.get("name")

    # Ensure the org actually exists (callers must create-then-issue, except
    # for the well-known default org which is always present).
    if db_service.get_org_by_id(org_id) is None:
        return jsonify({"error": f"Org {org_id!r} does not exist"}), 404

    if scopes is not None and not isinstance(scopes, list):
        return jsonify({"error": "scopes must be a list of strings"}), 400

    out = service.issue_token(
        org_id=org_id, project_id=project_id, scopes=scopes, name=name,
    )
    return jsonify(out), 201


@app.route("/api/v1/tokens", methods=["GET"])
@require_admin
def tokens_list():
    org_id = request.args.get("org_id")
    items = db_service.list_tokens(org_id=org_id)
    return jsonify({"items": [t.to_dict() for t in items]})


@app.route("/api/v1/tokens/<token_id>", methods=["DELETE"])
@require_admin
def tokens_revoke(token_id):
    ok = db_service.revoke_token(token_id)
    if not ok:
        return jsonify({"error": "Token not found or already revoked"}), 404
    return jsonify({"ok": True}), 200


@app.route("/api/v1/tokens/verify", methods=["POST"])
def tokens_verify():
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    if not token:
        return jsonify({"error": "token is required"}), 400
    result, is_valid = service.verify_token(token)
    return jsonify(result), 200 if is_valid else 401


# ===================================================================
# Auth — whoami
# ===================================================================
#
# Cheap, well-known endpoint a client can call to confirm the resolved scope
# (org_id, org_slug, project_id_scope, scopes) of the bearer it's about to
# use. The CLI uses this for pre-flight checks: if the URL the user typed
# carries an ``<org_slug>/<project_slug>`` that disagrees with the token's
# real scope, we want to fail loudly *before* writing data under a different
# org than the user expected.

@app.route("/api/v1/auth/whoami", methods=["GET"])
@require_auth
def auth_whoami():
    return jsonify(service.whoami(g.token_ctx))


# ===================================================================
# Orgs (admin-only convenience helpers)
# ===================================================================

@app.route("/api/v1/orgs", methods=["POST"])
@require_admin
def orgs_create():
    body = request.get_json(silent=True) or {}
    slug = body.get("slug")
    if not slug:
        return jsonify({"error": "slug is required"}), 400
    existing = db_service.get_org_by_slug(slug)
    if existing is not None:
        return jsonify(existing.to_dict()), 200
    org = db_service.create_org(slug=slug, name=body.get("name"))
    return jsonify(org.to_dict()), 201


# ===================================================================
# Projects — registration + read
# ===================================================================
#
# Project identity is the wire ``project_id`` slug, scoped within an org by
# the (org_id, project_id) UNIQUE on ``projects``. The remote URL the CLI
# binds to has the shape ``<scheme>://<host>/<org_slug>/<project_id>``.
# Registration is explicit: clients must POST here (or rely on the implicit
# upsert during sync, which only the admin path uses) before pushing data.

def _resolve_caller_for_project_admin() -> tuple[str | None, tuple[Response, int] | None]:
    """Resolve the org_id of the caller for project-admin routes.

    Honours either:
      - ``X-Admin-Secret`` matching ``ADMIN_SECRET`` plus ``org_id`` and/or
        ``org_slug`` in body or query. ``org_slug`` is preferred when present
        — admin tooling should refer to orgs by their human slug rather than
        a UUID — and if both are given they must point at the same org.
      - a Bearer token that is org-scoped and has ``projects:write``. If the
        body carries an ``org_slug``, it must match the slug of the token's
        org or we return 403 ``org_slug_mismatch``.

    Returns ``(org_id, None)`` on success or ``(None, error_response)``.
    """
    body = request.get_json(silent=True) or {}
    body_org_slug = body.get("org_slug") or request.args.get("org_slug")

    admin_secret = request.headers.get("X-Admin-Secret", "")
    if admin_secret and admin_secret == service.ADMIN_SECRET:
        org_id = body.get("org_id") or request.args.get("org_id")
        org_obj = None
        if body_org_slug:
            org_obj = db_service.get_org_by_slug(body_org_slug)
            if org_obj is None:
                return None, (jsonify({
                    "error": f"Org with slug {body_org_slug!r} does not exist",
                    "code": "org_not_found",
                }), 404)
            if org_id and str(org_obj.id) != org_id:
                return None, (jsonify({
                    "error": (
                        f"org_id and org_slug refer to different orgs: "
                        f"slug {body_org_slug!r} resolves to {org_obj.id} "
                        f"but org_id was {org_id!r}"
                    ),
                    "code": "org_slug_mismatch",
                }), 400)
            return str(org_obj.id), None
        # No slug provided; fall back to org_id (or default).
        org_id = org_id or db_service.DEFAULT_ORG_ID
        if db_service.get_org_by_id(org_id) is None:
            return None, (jsonify({"error": f"Org {org_id!r} does not exist"}), 404)
        return org_id, None

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, (
            jsonify({"error": "Project admin requires X-Admin-Secret or org-scoped Bearer token"}),
            401,
        )
    ctx = service.resolve_token(auth[7:])
    if ctx is None:
        return None, (jsonify({"error": "Invalid or revoked token"}), 401)
    if not service.can_create_project(ctx):
        return None, (
            jsonify({
                "error": "Token must be org-scoped (no project_id_scope) and carry the "
                         "'projects:write' scope. Admin can also use X-Admin-Secret."
            }),
            403,
        )
    if body_org_slug:
        caller_org = db_service.get_org_by_id(ctx.org_id)
        caller_slug = caller_org.slug if caller_org else None
        if caller_slug != body_org_slug:
            return None, (jsonify({
                "error": (
                    f"Token belongs to org {caller_slug!r} but request targets "
                    f"org {body_org_slug!r}. The org slug in the URL must match "
                    "the token's org."
                ),
                "code": "org_slug_mismatch",
                "expected": caller_slug,
                "got": body_org_slug,
            }), 403)
    return ctx.org_id, None


@app.route("/api/v1/projects", methods=["POST"])
def projects_create():
    org_id, err = _resolve_caller_for_project_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    try:
        proj = db_service.create_project(
            org_id=org_id,
            project_id=project_id,
            name=body.get("name"),
            description=body.get("description"),
        )
    except db_service.ProjectExistsError:
        return jsonify({
            "error": f"Project {project_id!r} already exists in this org",
            "code": "project_exists",
        }), 409
    except db_service.InvalidProjectSlugError as e:
        return jsonify({"error": str(e), "code": "invalid_slug"}), 400
    return jsonify(proj.to_dict()), 201


@app.route("/api/v1/projects", methods=["GET"])
@require_auth
def projects_list():
    items = db_service.list_projects(g.org_id)
    if g.project_id_scope is not None:
        items = [p for p in items if p.project_id == g.project_id_scope]
    return jsonify({"items": [p.to_dict() for p in items]})


@app.route("/api/v1/projects/<project_id>", methods=["GET"])
@require_auth
def projects_get(project_id):
    if not service.assert_project_in_token_scope(g.token_ctx, project_id):
        return jsonify({"error": "Token is not scoped to this project"}), 403
    proj = db_service.get_project(g.org_id, project_id)
    if proj is None:
        return jsonify({"error": "Project not found"}), 404
    return jsonify(proj.to_dict())


# ===================================================================
# Sync — Traces
# ===================================================================

@app.route("/api/v1/sync/traces", methods=["POST"])
@require_auth
def sync_traces_push():
    """Bulk upsert traces."""
    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400
    project_id, err = _project_id_from_request()
    if err:
        return err

    count = service.sync_upsert_traces(g.org_id, project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/traces", methods=["GET"])
@require_auth
def sync_traces_pull():
    project_id, err = _project_id_from_request()
    if err:
        return err
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_traces_since(
        g.org_id, project_id, since=since, limit=limit,
    )
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Sync — Ledgers
# ===================================================================

@app.route("/api/v1/sync/ledgers", methods=["POST"])
@require_auth
def sync_ledgers_push():
    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    project_id, err = _project_id_from_request()
    if err:
        return err

    count = service.sync_upsert_ledgers(g.org_id, project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/ledgers", methods=["GET"])
@require_auth
def sync_ledgers_pull():
    project_id, err = _project_id_from_request()
    if err:
        return err
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_ledgers_since(
        g.org_id, project_id, since=since, limit=limit,
    )
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Sync — Commit Links
# ===================================================================

@app.route("/api/v1/sync/commit-links", methods=["POST"])
@require_auth
def sync_commit_links_push():
    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    project_id, err = _project_id_from_request()
    if err:
        return err

    count = service.sync_upsert_commit_links(g.org_id, project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/commit-links", methods=["GET"])
@require_auth
def sync_commit_links_pull():
    project_id, err = _project_id_from_request()
    if err:
        return err
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_commit_links_since(
        g.org_id, project_id, since=since, limit=limit,
    )
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Sync — Conversations
# ===================================================================

@app.route("/api/v1/sync/conversations", methods=["POST"])
@require_auth
def sync_conversations_push():
    body = request.get_json(silent=True) or {}
    items = body.get("items", body.get("conversation_contents", []))
    project_id, err = _project_id_from_request()
    if err:
        return err

    try:
        count = service.sync_upsert_conversations(g.org_id, project_id, g.user_id, items)
    except db_service.MissingBlobForConversationError as e:
        return jsonify({
            "error": "Referenced blob is missing; upload it via POST /api/v1/blobs first",
            "content_sha256": e.sha256,
        }), 400
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/conversations", methods=["GET"])
@require_auth
def sync_conversations_pull():
    project_id, err = _project_id_from_request()
    if err:
        return err
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_conversations_since(
        g.org_id, project_id, since=since, limit=limit,
    )
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Sync — Summaries
# ===================================================================

@app.route("/api/v1/sync/summaries", methods=["POST"])
@require_auth
def sync_summaries_push():
    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    project_id, err = _project_id_from_request()
    if err:
        return err
    count = service.sync_upsert_summaries(g.org_id, project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/summaries", methods=["GET"])
@require_auth
def sync_summaries_pull():
    project_id, err = _project_id_from_request()
    if err:
        return err
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_summaries_since(
        g.org_id, project_id, since=since, limit=limit,
    )
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Direct fetch by ID (read-only)
# ===================================================================

@app.route("/api/v1/traces/<trace_id>", methods=["GET"])
@require_auth
def get_trace(trace_id):
    project_id, err = _project_id_from_request()
    if err:
        return err
    result = db_service.get_trace(g.org_id, project_id, trace_id)
    if result is None:
        return jsonify({"error": "Trace not found"}), 404
    return jsonify(result)


@app.route("/api/v1/ledgers/<commit_sha>", methods=["GET"])
@require_auth
def get_ledger(commit_sha):
    project_id, err = _project_id_from_request()
    if err:
        return err
    ledger = db_service.get_ledger(g.org_id, project_id, commit_sha)
    if ledger is None:
        return jsonify({"error": "Ledger not found"}), 404
    return jsonify(ledger)


@app.route("/api/v1/conversations/<path:conversation_id>", methods=["GET"])
@require_auth
def get_conversation(conversation_id):
    project_id, err = _project_id_from_request()
    if err:
        return err
    pointer = db_service.get_conversation_pointer(g.org_id, project_id, conversation_id)
    if pointer is None:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(pointer)


# ===================================================================
# Blobs (content-addressed)
# ===================================================================

@app.route("/api/v1/blobs/<sha256>", methods=["HEAD"])
@require_auth
def blob_head(sha256):
    if service.blob_present(sha256):
        return ("", 200)
    return ("", 404)


@app.route("/api/v1/blobs", methods=["POST"])
@require_auth
def blob_post():
    raw = request.get_data(cache=False, as_text=False)
    if not raw:
        return jsonify({"error": "Empty body"}), 400
    try:
        out = service.store_blob(raw)
    except ValueError as e:
        return jsonify({"error": str(e)}), 413
    return jsonify(out), 201


@app.route("/api/v1/blobs/<sha256>", methods=["GET"])
@require_auth
def blob_get(sha256):
    raw = service.fetch_blob(sha256)
    if raw is None:
        return jsonify({"error": "Blob not found"}), 404
    return Response(raw, mimetype="application/octet-stream")


# ===================================================================
# Main
# ===================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
