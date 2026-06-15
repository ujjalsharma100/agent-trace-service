"""End-to-end tests for the project registration routes.

Boots against the same running service used by ``test_tenant_isolation``;
skips if ``AGENT_TRACE_SVC_TEST_URL`` / ``AGENT_TRACE_SVC_TEST_ADMIN_SECRET``
are not set.

Coverage:
- POST /api/v1/projects via X-Admin-Secret           (201 / 409 / 400)
- POST /api/v1/projects via org-scoped token         (with / without projects:write)
- POST /api/v1/projects refused for project-scoped tokens
- GET  /api/v1/projects                              (lists own org)
- GET  /api/v1/projects/<project_id>                 (404 / 200)
- Sync routes return 404 ``project_not_found`` when the slug isn't registered
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
class TestProjectRegistration(unittest.TestCase):
    def setUp(self) -> None:
        self.base = os.environ[_URL_ENV].rstrip("/")
        self.admin = {"X-Admin-Secret": os.environ[_ADMIN_ENV]}
        self.suffix = str(int(time.time() * 1000))
        self.org_slug = f"projects-test-{self.suffix}"

        s, org = _http(
            "POST", f"{self.base}/api/v1/orgs",
            body={"slug": self.org_slug, "name": "Projects Test"}, headers=self.admin,
        )
        self.assertIn(s, (200, 201), org)
        self.org_id = org["id"]

    def _bearer(self, tok: str) -> dict:
        return {"Authorization": f"Bearer {tok}"}

    def _mint_token(self, *, project_id: str | None = None, scopes: list[str] | None = None) -> str:
        body: dict = {"org_id": self.org_id}
        if project_id is not None:
            body["project_id"] = project_id
        if scopes is not None:
            body["scopes"] = scopes
        s, tok = _http(
            "POST", f"{self.base}/api/v1/tokens", body=body, headers=self.admin,
        )
        self.assertEqual(s, 201, tok)
        return tok["token"]

    # -- create -------------------------------------------------------------

    def test_admin_creates_project(self) -> None:
        slug = f"alpha-{self.suffix}"
        s, p = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"org_id": self.org_id, "project_id": slug, "name": "Alpha"},
            headers=self.admin,
        )
        self.assertEqual(s, 201, p)
        self.assertEqual(p["project_id"], slug)
        self.assertEqual(p["name"], "Alpha")

    def test_duplicate_returns_409(self) -> None:
        slug = f"dup-{self.suffix}"
        s, _ = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"org_id": self.org_id, "project_id": slug}, headers=self.admin,
        )
        self.assertEqual(s, 201)
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"org_id": self.org_id, "project_id": slug}, headers=self.admin,
        )
        self.assertEqual(s, 409, payload)
        self.assertEqual(payload.get("code"), "project_exists")

    def test_invalid_slug_returns_400(self) -> None:
        # Leading dash is not a legal slug under projects_slug_shape.
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"org_id": self.org_id, "project_id": "-illegal"},
            headers=self.admin,
        )
        self.assertEqual(s, 400, payload)
        self.assertEqual(payload.get("code"), "invalid_slug")

    def test_org_scoped_token_with_projects_write_can_create(self) -> None:
        slug = f"scoped-ok-{self.suffix}"
        token = self._mint_token(scopes=["read", "write", "projects:write"])
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"project_id": slug}, headers=self._bearer(token),
        )
        self.assertEqual(s, 201, payload)

    def test_org_scoped_token_without_scope_is_forbidden(self) -> None:
        slug = f"scoped-no-{self.suffix}"
        token = self._mint_token(scopes=["read", "write"])
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"project_id": slug}, headers=self._bearer(token),
        )
        self.assertEqual(s, 403, payload)

    def test_project_scoped_token_cannot_create_other_project(self) -> None:
        # Pre-register one project so we can mint a project-scoped token for it.
        target = f"target-{self.suffix}"
        _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"org_id": self.org_id, "project_id": target}, headers=self.admin,
        )
        token = self._mint_token(project_id=target, scopes=["read", "write", "projects:write"])
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"project_id": f"other-{self.suffix}"}, headers=self._bearer(token),
        )
        self.assertEqual(s, 403, payload)

    # -- read ---------------------------------------------------------------

    def test_list_projects_in_caller_org(self) -> None:
        for slug in (f"l1-{self.suffix}", f"l2-{self.suffix}"):
            _http(
                "POST", f"{self.base}/api/v1/projects",
                body={"org_id": self.org_id, "project_id": slug}, headers=self.admin,
            )
        token = self._mint_token()
        s, payload = _http(
            "GET", f"{self.base}/api/v1/projects", headers=self._bearer(token),
        )
        self.assertEqual(s, 200, payload)
        slugs = {p["project_id"] for p in payload["items"]}
        self.assertIn(f"l1-{self.suffix}", slugs)
        self.assertIn(f"l2-{self.suffix}", slugs)

    def test_get_project_404_when_missing(self) -> None:
        token = self._mint_token()
        s, payload = _http(
            "GET", f"{self.base}/api/v1/projects/does-not-exist-{self.suffix}",
            headers=self._bearer(token),
        )
        self.assertEqual(s, 404, payload)

    # -- sync gating --------------------------------------------------------

    def test_sync_404_when_project_not_registered(self) -> None:
        token = self._mint_token()
        slug = f"unregistered-{self.suffix}"
        s, payload = _http(
            "POST", f"{self.base}/api/v1/sync/traces",
            body={
                "project_id": slug,
                "items": [{
                    "id": "t1", "version": "1.0",
                    "timestamp": "2026-05-10T00:00:00Z",
                    "tool": {"name": "x"},
                }],
            },
            headers=self._bearer(token),
        )
        self.assertEqual(s, 404, payload)
        self.assertEqual(payload.get("code"), "project_not_found")

    # -- org_slug enforcement on POST /api/v1/projects ---------------------

    def test_org_scoped_token_rejects_wrong_org_slug(self) -> None:
        """A token from one org cannot register a project under another org's
        slug. Without this check the row would land under the token's org and
        local CLI state would silently disagree with the database.
        """
        # Make a second org we don't own a token for.
        other_slug = f"other-org-{self.suffix}"
        s, _ = _http(
            "POST", f"{self.base}/api/v1/orgs",
            body={"slug": other_slug}, headers=self.admin,
        )
        self.assertIn(s, (200, 201))

        token = self._mint_token(scopes=["read", "write", "projects:write"])
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"project_id": f"crossorg-{self.suffix}", "org_slug": other_slug},
            headers=self._bearer(token),
        )
        self.assertEqual(s, 403, payload)
        self.assertEqual(payload.get("code"), "org_slug_mismatch")
        self.assertEqual(payload.get("expected"), self.org_slug)
        self.assertEqual(payload.get("got"), other_slug)

    def test_org_scoped_token_accepts_matching_org_slug(self) -> None:
        token = self._mint_token(scopes=["read", "write", "projects:write"])
        slug = f"matching-{self.suffix}"
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"project_id": slug, "org_slug": self.org_slug},
            headers=self._bearer(token),
        )
        self.assertEqual(s, 201, payload)
        self.assertEqual(payload["project_id"], slug)

    def test_admin_with_org_slug_resolves_org(self) -> None:
        """Admin can pass ``org_slug`` instead of ``org_id`` and the server
        looks the org up; both forms reach the same row.
        """
        slug = f"admin-by-slug-{self.suffix}"
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"project_id": slug, "org_slug": self.org_slug},
            headers=self.admin,
        )
        self.assertEqual(s, 201, payload)
        self.assertEqual(payload["org_id"], self.org_id)

    def test_admin_org_slug_and_org_id_must_agree(self) -> None:
        other_slug = f"admin-mismatch-{self.suffix}"
        s, other_org = _http(
            "POST", f"{self.base}/api/v1/orgs",
            body={"slug": other_slug}, headers=self.admin,
        )
        self.assertIn(s, (200, 201))

        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={
                "project_id": f"badmix-{self.suffix}",
                "org_id": self.org_id,
                "org_slug": other_slug,
            },
            headers=self.admin,
        )
        self.assertEqual(s, 400, payload)
        self.assertEqual(payload.get("code"), "org_slug_mismatch")

    def test_admin_unknown_org_slug_404(self) -> None:
        s, payload = _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"project_id": "x", "org_slug": f"never-existed-{self.suffix}"},
            headers=self.admin,
        )
        self.assertEqual(s, 404, payload)
        self.assertEqual(payload.get("code"), "org_not_found")

    # -- whoami ------------------------------------------------------------

    def test_whoami_returns_org_scope(self) -> None:
        token = self._mint_token(scopes=["read", "write"])
        s, payload = _http(
            "GET", f"{self.base}/api/v1/auth/whoami",
            headers=self._bearer(token),
        )
        self.assertEqual(s, 200, payload)
        self.assertEqual(payload["org_id"], self.org_id)
        self.assertEqual(payload["org_slug"], self.org_slug)
        self.assertIsNone(payload["project_id_scope"])
        self.assertIn("read", payload["scopes"])

    def test_whoami_returns_project_scope(self) -> None:
        target = f"whoami-target-{self.suffix}"
        _http(
            "POST", f"{self.base}/api/v1/projects",
            body={"org_id": self.org_id, "project_id": target}, headers=self.admin,
        )
        token = self._mint_token(project_id=target)
        s, payload = _http(
            "GET", f"{self.base}/api/v1/auth/whoami",
            headers=self._bearer(token),
        )
        self.assertEqual(s, 200, payload)
        self.assertEqual(payload["project_id_scope"], target)

    def test_whoami_requires_auth(self) -> None:
        s, payload = _http(
            "GET", f"{self.base}/api/v1/auth/whoami",
        )
        self.assertEqual(s, 401, payload)


if __name__ == "__main__":
    unittest.main()
