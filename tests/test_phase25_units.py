"""Unit tests for Phase 25: API security headers."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


class SecurityHeaderUnitTests(unittest.TestCase):
    def setUp(self):
        for k in ("API_CSP", "API_ENABLE_HSTS", "API_HSTS_MAX_AGE"):
            os.environ.pop(k, None)

    def test_default_header_set(self):
        from api.security import security_headers

        h = security_headers()
        self.assertEqual(h["Content-Security-Policy"], "default-src 'none'; frame-ancestors 'none'")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertIn("Permissions-Policy", h)
        # HSTS off by default.
        self.assertNotIn("Strict-Transport-Security", h)

    def test_csp_override(self):
        from api.security import security_headers

        with mock.patch.dict(os.environ, {"API_CSP": "default-src 'self'"}):
            self.assertEqual(security_headers()["Content-Security-Policy"], "default-src 'self'")

    def test_hsts_enabled(self):
        from api.security import security_headers

        with mock.patch.dict(os.environ, {"API_ENABLE_HSTS": "1"}):
            h = security_headers()
            self.assertIn("Strict-Transport-Security", h)
            self.assertIn("max-age=63072000", h["Strict-Transport-Security"])
            self.assertIn("includeSubDomains", h["Strict-Transport-Security"])

    def test_hsts_max_age_override(self):
        from api.security import security_headers

        with mock.patch.dict(os.environ, {"API_ENABLE_HSTS": "1", "API_HSTS_MAX_AGE": "100"}):
            self.assertIn("max-age=100", security_headers()["Strict-Transport-Security"])


class SecurityHeaderIntegrationTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        for k in ("API_CSP", "API_ENABLE_HSTS"):
            os.environ.pop(k, None)

    def _client(self):
        from fastapi.testclient import TestClient

        from api.main import create_app
        return TestClient(create_app())

    def test_headers_present_on_healthz(self):
        r = self._client().get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("content-security-policy", r.headers)
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_headers_present_on_401(self):
        """Security headers must attach even to error responses."""
        r = self._client().get("/v1/me")  # no auth -> 401
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertIn("content-security-policy", r.headers)

    def test_hsts_present_when_enabled(self):
        with mock.patch.dict(os.environ, {"API_ENABLE_HSTS": "1"}):
            r = self._client().get("/healthz")
        self.assertIn("strict-transport-security", r.headers)

    def test_hsts_absent_by_default(self):
        r = self._client().get("/healthz")
        self.assertNotIn("strict-transport-security", r.headers)


if __name__ == "__main__":
    unittest.main()
