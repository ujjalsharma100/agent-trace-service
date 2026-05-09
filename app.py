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

    GET    /api/v1/sync/traces?project_id=&since=&limit=
    GET    /api/v1/sync/ledgers?project_id=&since=&limit=
    GET    /api/v1/sync/commit-links?project_id=&since=&limit=
    GET    /api/v1/sync/conversations?project_id=&since=&limit=

    GET    /api/v1/traces/<id>?project_id=
    GET    /api/v1/ledgers/<commit_sha>?project_id=
    GET    /api/v1/conversations/<url_hash>?project_id=

    HEAD   /api/v1/blobs/<sha256>
    POST   /api/v1/blobs                       (raw body)
    GET    /api/v1/blobs/<sha256>

    POST   /api/v1/tokens                      (admin: issue)
    GET    /api/v1/tokens                      (admin: list)
    DELETE /api/v1/tokens/<id>                 (admin: revoke)
    POST   /api/v1/tokens/verify               (verify a presented token)

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
    """Resolve the request's project_id and ensure the token scope permits it."""
    project_id = request.args.get("project_id")
    if project_id is None and request.method in ("POST",):
        body = request.get_json(silent=True) or {}
        project_id = body.get("project_id")
    if not project_id:
        return None, (jsonify({"error": "project_id is required"}), 400)
    if not service.assert_project_in_token_scope(g.token_ctx, project_id):
        return None, (jsonify({"error": "Token is not scoped to this project"}), 403)
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
            "get_trace": "GET /api/v1/traces/<id>?project_id=",
            "get_ledger": "GET /api/v1/ledgers/<commit_sha>?project_id=",
            "get_conversation": "GET /api/v1/conversations/<url_hash>?project_id=",
            "blob_head": "HEAD /api/v1/blobs/<sha256>",
            "blob_post": "POST /api/v1/blobs",
            "blob_get": "GET /api/v1/blobs/<sha256>",
            "tokens_admin": "POST/GET /api/v1/tokens, DELETE /api/v1/tokens/<id> (X-Admin-Secret)",
            "tokens_verify": "POST /api/v1/tokens/verify",
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

    count = service.sync_upsert_conversations(g.org_id, project_id, g.user_id, items)
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


@app.route("/api/v1/conversations/<path:url_hash>", methods=["GET"])
@require_auth
def get_conversation(url_hash):
    project_id, err = _project_id_from_request()
    if err:
        return err
    pointer = db_service.get_conversation_pointer(g.org_id, project_id, url_hash)
    if pointer is None:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"url": url_hash, **pointer})


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
