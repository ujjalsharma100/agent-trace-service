"""
Signed-gateway authentication for agent-trace-service.

This is an **optional, off-by-default** trust path for deployments that put a
control plane in front of the datastore (for example a hosted product that
reverse-proxies CLI traffic). The gateway authenticates the end user and
enforces scopes itself, then signs the proxied request with a shared secret so
this service can trust the asserted ``(org, user, project)`` identity without a
second token round-trip.

Canonical signing string (newline-joined, in this exact order)::

    <org_id>\n<user_id>\n<project_id>\n<sha256-hex(body)>

``project_id`` is the empty string when absent; ``body`` is the raw request
bytes (empty for GET/HEAD). The signature is ``HMAC-SHA256`` keyed by
``AGENT_TRACE_GATEWAY_SECRET``, hex-encoded, presented in the
``X-AgentTrace-Signature`` header alongside ``X-AgentTrace-Org`` /
``X-AgentTrace-User`` / ``X-AgentTrace-Project``.

For backwards compatibility the legacy ``X-Curio-*`` header names are accepted
as a **deprecated alias**; the canonical ``X-AgentTrace-*`` names take
precedence when both are present.

When the shared secret is unset the gateway path is disabled and requests fall
through to normal bearer-token auth.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable, Optional

from model import TokenContext

# Canonical header names first, deprecated ``X-Curio-*`` alias second.
_ORG_HEADERS = ("X-AgentTrace-Org", "X-Curio-Org")
_USER_HEADERS = ("X-AgentTrace-User", "X-Curio-User")
_PROJECT_HEADERS = ("X-AgentTrace-Project", "X-Curio-Project")
_SIGNATURE_HEADERS = ("X-AgentTrace-Signature", "X-Curio-Signature")

# Scopes a gateway-signed request is granted. The gateway has already
# authenticated the user and enforced their effective access before signing, so
# the datastore trusts it for the read/write data plane. Project registration /
# deletion still requires the admin path (``X-Admin-Secret``).
_GATEWAY_SCOPES = ["read", "write"]


class GatewaySignatureError(Exception):
    """A gateway-signed request was presented but failed verification."""


def _first_header(get_header: Callable[[str], Optional[str]], names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = get_header(name)
        if value:
            return value
    return None


def canonical_string(
    org_id: str,
    user_id: str,
    project_id: str | None,
    body: bytes,
) -> str:
    """Build the canonical string the gateway signs."""
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return "\n".join([org_id or "", user_id or "", project_id or "", body_hash])


def compute_signature(
    secret: str,
    org_id: str,
    user_id: str,
    project_id: str | None,
    body: bytes,
) -> str:
    """HMAC-SHA256 hex digest of the canonical string. Mirrors the gateway."""
    canonical = canonical_string(org_id, user_id, project_id, body)
    return hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def try_resolve_gateway(
    get_header: Callable[[str], Optional[str]],
    body: bytes,
    secret: str | None,
) -> TokenContext | None:
    """Resolve a gateway-signed request to a ``TokenContext``.

    Returns ``None`` when no gateway signature is present, or when ``secret`` is
    unset (gateway mode disabled) — in both cases the caller should fall back to
    bearer-token auth. Raises :class:`GatewaySignatureError` when a signature
    *is* presented but is missing required headers or fails verification.
    """
    signature = _first_header(get_header, _SIGNATURE_HEADERS)
    if not signature:
        return None
    if not secret:
        # A signature was presented but this service has no shared secret to
        # verify it against — gateway mode is off. Fall through to bearer.
        return None

    org_id = _first_header(get_header, _ORG_HEADERS)
    user_id = _first_header(get_header, _USER_HEADERS)
    project_id = _first_header(get_header, _PROJECT_HEADERS)
    if not org_id or not user_id:
        raise GatewaySignatureError("gateway signature missing org/user headers")

    expected = compute_signature(secret, org_id, user_id, project_id, body)
    if not hmac.compare_digest(expected, signature):
        raise GatewaySignatureError("gateway signature mismatch")

    # ``token_id`` carries the real end-user id so audit columns (traces.user_id
    # etc.) attribute writes to the human, not a token. ``project_id_scope``
    # pins the request to the signed project for defence in depth (the gateway
    # also injects the same ``project_id`` into the body/query).
    return TokenContext(
        token_id=user_id,
        org_id=org_id,
        project_id_scope=project_id or None,
        scopes=list(_GATEWAY_SCOPES),
    )
