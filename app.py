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

    POST   /api/v1/tokens/generate
    GET    /health

Run:
    python app.py                         (dev)
    gunicorn app:app -b 0.0.0.0:5000      (production)
"""

import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request

import agent_trace_service as service
import database_service as db_service

load_dotenv()

PORT = int(os.environ.get("PORT", "5000"))

app = Flask(__name__)
app.teardown_appcontext(db_service.close_db)


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        user_id = service.decode_token(auth[7:])
        if not user_id:
            return jsonify({"error": "Invalid or expired token"}), 401
        g.user_id = user_id
        return f(*args, **kwargs)
    return wrapper


# ===================================================================
# Root / Health / Tokens
# ===================================================================

@app.route("/")
def root():
    return jsonify({
        "name": "agent-trace-service",
        "version": "1.0.0",
        "description": "Pure datastore — all domain logic lives in the CLI.",
        "endpoints": {
            "health": "GET /health",
            "sync_traces": "POST/GET /api/v1/sync/traces",
            "sync_ledgers": "POST/GET /api/v1/sync/ledgers",
            "sync_commit_links": "POST/GET /api/v1/sync/commit-links",
            "sync_conversations": "POST/GET /api/v1/sync/conversations",
            "get_trace": "GET /api/v1/traces/<id>?project_id=",
            "get_ledger": "GET /api/v1/ledgers/<commit_sha>?project_id=",
            "get_conversation": "GET /api/v1/conversations/<url_hash>?project_id=",
            "generate_token": "POST /api/v1/tokens/generate",
        },
    })


@app.route("/health")
def health():
    try:
        result = service.health_check()
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "db": "disconnected", "error": str(e)}), 503


@app.route("/api/v1/tokens/generate", methods=["POST"])
def tokens_generate():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    return jsonify(service.handle_generate_token(user_id))


@app.route("/api/v1/tokens/verify", methods=["POST"])
def tokens_verify():
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    if not token:
        return jsonify({"error": "token is required"}), 400
    result, is_valid = service.handle_verify_token(token)
    return jsonify(result), 200 if is_valid else 401


# ===================================================================
# Sync — Traces
# ===================================================================

@app.route("/api/v1/sync/traces", methods=["POST"])
@require_auth
def sync_traces_push():
    """Bulk upsert traces."""
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    items = body.get("items", [])
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400

    count = service.sync_upsert_traces(project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/traces", methods=["GET"])
@require_auth
def sync_traces_pull():
    """Fetch traces since a timestamp."""
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_traces_since(project_id, since=since, limit=limit)
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Sync — Ledgers
# ===================================================================

@app.route("/api/v1/sync/ledgers", methods=["POST"])
@require_auth
def sync_ledgers_push():
    """Bulk upsert ledgers."""
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    items = body.get("items", [])
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    count = service.sync_upsert_ledgers(project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/ledgers", methods=["GET"])
@require_auth
def sync_ledgers_pull():
    """Fetch ledgers since a timestamp."""
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_ledgers_since(project_id, since=since, limit=limit)
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Sync — Commit Links
# ===================================================================

@app.route("/api/v1/sync/commit-links", methods=["POST"])
@require_auth
def sync_commit_links_push():
    """Bulk upsert commit links."""
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    items = body.get("items", [])
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    count = service.sync_upsert_commit_links(project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/commit-links", methods=["GET"])
@require_auth
def sync_commit_links_pull():
    """Fetch commit links since a timestamp."""
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_commit_links_since(project_id, since=since, limit=limit)
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Sync — Conversations
# ===================================================================

@app.route("/api/v1/sync/conversations", methods=["POST"])
@require_auth
def sync_conversations_push():
    """Bulk upsert conversation contents."""
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    items = body.get("items", body.get("conversation_contents", []))
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    count = service.sync_upsert_conversations(project_id, g.user_id, items)
    return jsonify({"ok": True, "count": count}), 200


@app.route("/api/v1/sync/conversations", methods=["GET"])
@require_auth
def sync_conversations_pull():
    """Fetch conversations since a timestamp."""
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", "500")), 1000)

    items, max_timestamp = db_service.list_conversations_since(project_id, since=since, limit=limit)
    return jsonify({"items": items, "max_timestamp": max_timestamp})


# ===================================================================
# Direct fetch by ID (read-only)
# ===================================================================

@app.route("/api/v1/traces/<trace_id>", methods=["GET"])
@require_auth
def get_trace(trace_id):
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    result = db_service.get_trace(project_id, trace_id)
    if result is None:
        return jsonify({"error": "Trace not found"}), 404
    return jsonify(result)


@app.route("/api/v1/ledgers/<commit_sha>", methods=["GET"])
@require_auth
def get_ledger(commit_sha):
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    ledger = db_service.get_ledger(project_id, commit_sha)
    if ledger is None:
        return jsonify({"error": "Ledger not found"}), 404
    return jsonify(ledger)


@app.route("/api/v1/conversations/<path:url_hash>", methods=["GET"])
@require_auth
def get_conversation(url_hash):
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    content = db_service.get_conversation_content(project_id, url_hash)
    if content is None:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"url": url_hash, "content": content})


# ===================================================================
# Main
# ===================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
