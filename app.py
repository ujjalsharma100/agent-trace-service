#!/usr/bin/env python3
"""
agent-trace-service  —  Flask API for storing and querying AI agent traces.

This module only defines HTTP endpoints.  Business logic lives in
agent_trace_service.py and database access in database_service.py.

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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("PORT", "5000"))

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

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
# Routes — Root
# ===================================================================

@app.route("/")
def root():
    return jsonify({
        "name": "agent-trace-service",
        "version": "0.2.0",
        "docs": {
            "health": "GET /health",
            "ingest_trace": "POST /api/v1/traces",
            "batch_ingest": "POST /api/v1/traces/batch",
            "list_traces": "GET /api/v1/traces?project_id=<id>",
            "get_trace": "GET /api/v1/traces/<traceId>?project_id=<id>",
            "sync_conversation": "POST /api/v1/conversations/sync",
            "project_info": "GET /api/v1/projects/<projectId>",
            "create_project": "POST /api/v1/projects",
            "generate_token": "POST /api/v1/tokens/generate",
            "verify_token": "POST /api/v1/tokens/verify",
        },
    })


# ===================================================================
# Routes — Health
# ===================================================================

@app.route("/health")
def health():
    try:
        result = service.health_check()
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "status": "error",
            "db": "disconnected",
            "error": str(e),
        }), 503


# ===================================================================
# Routes — Tokens
# ===================================================================

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
# Routes — Projects
# ===================================================================

@app.route("/api/v1/projects/<project_id>", methods=["GET"])
@require_auth
def get_project(project_id):
    result = service.get_project_detail(project_id)
    if result is None:
        return jsonify({"error": "Project not found"}), 404
    return jsonify(result)


@app.route("/api/v1/projects", methods=["POST"])
@require_auth
def create_project():
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    result = service.create_or_update_project(
        project_id,
        name=body.get("name"),
        description=body.get("description"),
    )
    return jsonify(result), 201


# ===================================================================
# Routes — Traces
# ===================================================================

@app.route("/api/v1/traces", methods=["POST"])
@require_auth
def ingest_trace():
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    trace = body.get("trace")

    if not project_id or not trace or not trace.get("id") or not trace.get("timestamp"):
        return jsonify({"error": "project_id, trace.id, and trace.timestamp are required"}), 400

    trace_id = service.ingest_trace(
        project_id=project_id,
        user_id=g.user_id,
        trace=trace,
        conversation_contents=body.get("conversation_contents"),
    )
    return jsonify({"ok": True, "trace_id": trace_id}), 201


@app.route("/api/v1/traces/batch", methods=["POST"])
@require_auth
def batch_ingest():
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    items = body.get("items", [])
    if not project_id or not items:
        return jsonify({"error": "project_id and items are required"}), 400

    trace_ids = service.batch_ingest(project_id, g.user_id, items)
    return jsonify({"ok": True, "count": len(trace_ids), "trace_ids": trace_ids}), 201


@app.route("/api/v1/traces", methods=["GET"])
@require_auth
def list_traces():
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id query parameter is required"}), 400

    result = service.query_traces(
        project_id,
        since=request.args.get("since"),
        until=request.args.get("until"),
        limit=int(request.args.get("limit", "50")),
        offset=int(request.args.get("offset", "0")),
    )
    return jsonify(result)


@app.route("/api/v1/traces/<trace_id>", methods=["GET"])
@require_auth
def get_trace(trace_id):
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id query parameter is required"}), 400

    result = service.get_trace_detail(project_id, trace_id)
    if result is None:
        return jsonify({"error": "Trace not found"}), 404
    return jsonify(result)


# ===================================================================
# Routes — Conversation sync (no trace)
# ===================================================================

@app.route("/api/v1/conversations/sync", methods=["POST"])
@require_auth
def sync_conversation():
    """Upsert conversation contents only. Used when the agent has finished a response (e.g. afterAgentResponse)."""
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    conversation_contents = body.get("conversation_contents")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    if not isinstance(conversation_contents, list):
        return jsonify({"error": "conversation_contents must be a list"}), 400

    service.sync_conversation_contents(
        project_id=project_id,
        user_id=g.user_id,
        conversation_contents=conversation_contents,
    )
    return jsonify({"ok": True}), 200


# ===================================================================
# Main
# ===================================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
