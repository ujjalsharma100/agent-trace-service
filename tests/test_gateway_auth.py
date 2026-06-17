"""Unit tests for the signed-gateway auth path (gateway_auth).

Pure, in-process tests — no network, no database. They assert the wire contract
a control-plane gateway must honour: HMAC-SHA256 over
``org\\nuser\\nproject\\nsha256(body)`` keyed by ``AGENT_TRACE_GATEWAY_SECRET``,
carried in ``X-AgentTrace-*`` headers (with ``X-Curio-*`` as a deprecated alias).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gateway_auth  # noqa: E402

SECRET = "test-gateway-secret-min-32-chars-long-xx"
ORG = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"
PROJECT = "my-project"


def _headers(org=ORG, user=USER, project=PROJECT, body=b"", *, prefix="X-AgentTrace"):
    """Build a correctly-signed gateway header set keyed by ``prefix``."""
    sig = gateway_auth.compute_signature(SECRET, org, user, project, body)
    return {
        f"{prefix}-Org": org,
        f"{prefix}-User": user,
        f"{prefix}-Project": project,
        f"{prefix}-Signature": sig,
    }


def _getter(headers):
    return lambda name: headers.get(name)


class GatewayAuthTests(unittest.TestCase):
    def test_valid_signature_resolves_identity(self):
        body = b'{"items": []}'
        ctx = gateway_auth.try_resolve_gateway(_getter(_headers(body=body)), body, SECRET)
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertEqual(ctx.org_id, ORG)
        self.assertEqual(ctx.token_id, USER)  # audit attribution = real user
        self.assertEqual(ctx.project_id_scope, PROJECT)
        self.assertEqual(ctx.scopes, ["read", "write"])

    def test_curio_alias_headers_accepted(self):
        body = b"payload"
        headers = _headers(body=body, prefix="X-Curio")
        ctx = gateway_auth.try_resolve_gateway(_getter(headers), body, SECRET)
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertEqual(ctx.org_id, ORG)

    def test_agenttrace_takes_precedence_over_curio(self):
        body = b""
        headers = _headers(body=body)  # canonical, correctly signed
        # Add bogus X-Curio-* that, if used, would change the identity.
        headers["X-Curio-Org"] = "00000000-0000-0000-0000-000000000000"
        ctx = gateway_auth.try_resolve_gateway(_getter(headers), body, SECRET)
        assert ctx is not None
        self.assertEqual(ctx.org_id, ORG)

    def test_no_signature_returns_none(self):
        # No gateway headers at all → fall back to bearer (None).
        self.assertIsNone(
            gateway_auth.try_resolve_gateway(_getter({}), b"", SECRET)
        )

    def test_unset_secret_disables_gateway(self):
        body = b"data"
        # A valid-looking signed request, but the service has no secret set.
        self.assertIsNone(
            gateway_auth.try_resolve_gateway(_getter(_headers(body=body)), body, "")
        )

    def test_bad_signature_raises(self):
        headers = _headers(body=b"original")
        with self.assertRaises(gateway_auth.GatewaySignatureError):
            # Body differs from what was signed → mismatch.
            gateway_auth.try_resolve_gateway(_getter(headers), b"tampered", SECRET)

    def test_wrong_secret_raises(self):
        body = b"x"
        headers = _headers(body=body)
        with self.assertRaises(gateway_auth.GatewaySignatureError):
            gateway_auth.try_resolve_gateway(_getter(headers), body, "a-different-secret")

    def test_missing_org_header_raises(self):
        headers = _headers()
        del headers["X-AgentTrace-Org"]
        with self.assertRaises(gateway_auth.GatewaySignatureError):
            gateway_auth.try_resolve_gateway(_getter(headers), b"", SECRET)

    def test_absent_project_resolves_org_scoped(self):
        body = b""
        sig = gateway_auth.compute_signature(SECRET, ORG, USER, None, body)
        headers = {
            "X-AgentTrace-Org": ORG,
            "X-AgentTrace-User": USER,
            "X-AgentTrace-Signature": sig,
        }
        ctx = gateway_auth.try_resolve_gateway(_getter(headers), body, SECRET)
        assert ctx is not None
        self.assertIsNone(ctx.project_id_scope)


if __name__ == "__main__":
    unittest.main()
