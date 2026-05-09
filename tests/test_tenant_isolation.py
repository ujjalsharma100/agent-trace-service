"""Two-org isolation test for agent-trace-service.

Boots the running service against the same Postgres used by the e2e compose
stack. Skip-if-not-set: ``AGENT_TRACE_SVC_TEST_URL`` (eg. ``http://127.0.0.1:8765``)
and ``AGENT_TRACE_SVC_TEST_ADMIN_SECRET``.

The single load-bearing security check for M0:

    1. Mint two tokens against two distinct orgs.
    2. Push a trace using each token to the same project_id (they collide
       only by string but live in different org rows).
    3. Pull with each token. Each token must see only its own row.
    4. A direct GET by trace_id with the wrong token returns 404, not 200.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import urllib.error
import urllib.request


_URL_ENV = "AGENT_TRACE_SVC_TEST_URL"
_ADMIN_ENV = "AGENT_TRACE_SVC_TEST_ADMIN_SECRET"


def _skip_reason() -> str | None:
    if not os.environ.get(_URL_ENV, "").strip():
        return f"set {_URL_ENV} to run (e.g. http://127.0.0.1:8765)"
    if not os.environ.get(_ADMIN_ENV, "").strip():
        return f"set {_ADMIN_ENV} to the running service's ADMIN_SECRET"
    return None


def _http(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return e.code, payload


@unittest.skipIf(_skip_reason(), _skip_reason() or "")
class TestTenantIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.base = os.environ[_URL_ENV].rstrip("/")
        self.admin = {"X-Admin-Secret": os.environ[_ADMIN_ENV]}
        self.suffix = str(int(time.time() * 1000))

        self.org_a_slug = f"isolation-test-a-{self.suffix}"
        self.org_b_slug = f"isolation-test-b-{self.suffix}"
        self.project_id = f"-tenant-isolation-{self.suffix}"

        # Create two orgs.
        s, org_a = _http(
            "POST", f"{self.base}/api/v1/orgs",
            body={"slug": self.org_a_slug, "name": "Org A"}, headers=self.admin,
        )
        self.assertIn(s, (200, 201), org_a)
        s, org_b = _http(
            "POST", f"{self.base}/api/v1/orgs",
            body={"slug": self.org_b_slug, "name": "Org B"}, headers=self.admin,
        )
        self.assertIn(s, (200, 201), org_b)
        self.org_a_id = org_a["id"]
        self.org_b_id = org_b["id"]

        # Mint one token per org, both org-scoped.
        s, tok_a = _http(
            "POST", f"{self.base}/api/v1/tokens",
            body={"org_id": self.org_a_id, "name": "iso-a"}, headers=self.admin,
        )
        self.assertEqual(s, 201, tok_a)
        s, tok_b = _http(
            "POST", f"{self.base}/api/v1/tokens",
            body={"org_id": self.org_b_id, "name": "iso-b"}, headers=self.admin,
        )
        self.assertEqual(s, 201, tok_b)
        self.token_a = tok_a["token"]
        self.token_b = tok_b["token"]

    def _bearer(self, tok: str) -> dict:
        return {"Authorization": f"Bearer {tok}"}

    def test_tokens_only_see_their_own_orgs_traces(self) -> None:
        trace_a = {
            "id": f"trace-a-{self.suffix}",
            "version": "1.0",
            "timestamp": "2026-05-09T10:00:00Z",
            "tool": {"name": "iso-test"},
            "vcs": {"revision": "deadbeef"},
            "marker": "from-org-a",
        }
        trace_b = {
            "id": f"trace-b-{self.suffix}",
            "version": "1.0",
            "timestamp": "2026-05-09T10:00:00Z",
            "tool": {"name": "iso-test"},
            "vcs": {"revision": "cafebabe"},
            "marker": "from-org-b",
        }

        # Push: each token writes to the SAME project_id string but distinct orgs.
        s, _ = _http(
            "POST", f"{self.base}/api/v1/sync/traces",
            body={"project_id": self.project_id, "items": [trace_a]},
            headers=self._bearer(self.token_a),
        )
        self.assertEqual(s, 200)
        s, _ = _http(
            "POST", f"{self.base}/api/v1/sync/traces",
            body={"project_id": self.project_id, "items": [trace_b]},
            headers=self._bearer(self.token_b),
        )
        self.assertEqual(s, 200)

        # Pull with token A — only trace_a visible.
        s, out_a = _http(
            "GET",
            f"{self.base}/api/v1/sync/traces?project_id={self.project_id}",
            headers=self._bearer(self.token_a),
        )
        self.assertEqual(s, 200)
        ids_a = {t.get("id") for t in out_a["items"]}
        self.assertIn(trace_a["id"], ids_a)
        self.assertNotIn(trace_b["id"], ids_a)

        # Pull with token B — only trace_b visible.
        s, out_b = _http(
            "GET",
            f"{self.base}/api/v1/sync/traces?project_id={self.project_id}",
            headers=self._bearer(self.token_b),
        )
        self.assertEqual(s, 200)
        ids_b = {t.get("id") for t in out_b["items"]}
        self.assertIn(trace_b["id"], ids_b)
        self.assertNotIn(trace_a["id"], ids_b)

        # Direct GET with the wrong token → 404, never 200 with leaked data.
        s, payload = _http(
            "GET",
            f"{self.base}/api/v1/traces/{trace_a['id']}?project_id={self.project_id}",
            headers=self._bearer(self.token_b),
        )
        self.assertEqual(s, 404, payload)

    def test_revoked_token_cannot_read(self) -> None:
        s, tok = _http(
            "POST", f"{self.base}/api/v1/tokens",
            body={"org_id": self.org_a_id, "name": "iso-a-revoke"},
            headers=self.admin,
        )
        self.assertEqual(s, 201)
        token = tok["token"]
        token_id = tok["id"]

        s, _ = _http(
            "GET",
            f"{self.base}/api/v1/sync/traces?project_id={self.project_id}",
            headers=self._bearer(token),
        )
        self.assertEqual(s, 200)

        s, _ = _http(
            "DELETE",
            f"{self.base}/api/v1/tokens/{token_id}",
            headers=self.admin,
        )
        self.assertEqual(s, 200)

        s, payload = _http(
            "GET",
            f"{self.base}/api/v1/sync/traces?project_id={self.project_id}",
            headers=self._bearer(token),
        )
        self.assertEqual(s, 401, payload)

    def test_project_scoped_token_blocked_on_other_project(self) -> None:
        s, tok = _http(
            "POST", f"{self.base}/api/v1/tokens",
            body={
                "org_id": self.org_a_id,
                "project_id": self.project_id,
                "name": "iso-a-scoped",
            },
            headers=self.admin,
        )
        self.assertEqual(s, 201, tok)
        scoped_token = tok["token"]

        s, payload = _http(
            "GET",
            f"{self.base}/api/v1/sync/traces?project_id=some-other-project",
            headers=self._bearer(scoped_token),
        )
        self.assertEqual(s, 403, payload)


if __name__ == "__main__":
    unittest.main()
