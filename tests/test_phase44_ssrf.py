"""SSRF guard for user-supplied URLs (utils.security.check_url_allowed).

A hosted multi-tenant deployment fetches user URLs server-side (url_ingest)
and POSTs to user-registered endpoints (webhooks) — both must refuse private/
internal targets (cloud metadata, localhost services, RFC-1918).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.session import reset_engine_for_testing  # noqa: E402


class CheckUrlAllowedTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SSRF_ALLOW_PRIVATE_URLS", None)

    def test_private_ip_literals_rejected(self):
        from utils.security import check_url_allowed
        for url in (
            "http://127.0.0.1:8000/v1/me",          # localhost API
            "http://169.254.169.254/latest/meta-data",  # cloud metadata
            "http://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://[::1]/",
            "http://0.0.0.0/",
        ):
            ok, reason = check_url_allowed(url)
            self.assertFalse(ok, url)
            self.assertIn("private or internal", reason)

    def test_public_ip_literal_allowed(self):
        from utils.security import check_url_allowed
        ok, _ = check_url_allowed("https://8.8.8.8/")
        self.assertTrue(ok)

    def test_non_http_schemes_rejected(self):
        from utils.security import check_url_allowed
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x"):
            ok, reason = check_url_allowed(url)
            self.assertFalse(ok, url)
            self.assertIn("http", reason)

    def test_unresolvable_hostname_allowed(self):
        # The fetch itself will fail; rejecting would break offline dev/tests.
        from utils.security import check_url_allowed
        ok, _ = check_url_allowed("https://no-such-host-xyz123.invalid/job")
        self.assertTrue(ok)

    def test_hostname_resolving_private_rejected(self):
        from utils import security
        fake = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with mock.patch.object(security.socket, "getaddrinfo", return_value=fake):
            ok, _ = security.check_url_allowed("https://internal.corp/x")
        self.assertFalse(ok)

    def test_bypass_env(self):
        from utils.security import check_url_allowed
        with mock.patch.dict(os.environ, {"SSRF_ALLOW_PRIVATE_URLS": "1"}):
            ok, _ = check_url_allowed("http://127.0.0.1:9999/dev-receiver")
        self.assertTrue(ok)

    def test_missing_host_rejected(self):
        from utils.security import check_url_allowed
        ok, _ = check_url_allowed("http:///path-only")
        self.assertFalse(ok)


class SinkWiringTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SSRF_ALLOW_PRIVATE_URLS", None)
        reset_engine_for_testing("sqlite:///:memory:")
        from services.rate_limit import reset_backend_for_testing
        reset_backend_for_testing()
        from services.auth import register_user
        self.user = register_user("ssrf@example.com", "Sup3rSecret!")

    def test_url_ingest_rejects_internal_target(self):
        from tools.url_ingest import fetch_job_posting
        with self.assertRaises(ValueError) as ctx:
            fetch_job_posting("http://169.254.169.254/latest/meta-data")
        self.assertIn("private or internal", str(ctx.exception))

    def test_webhook_registration_rejects_internal_target(self):
        from services.webhooks import WebhookError, register_webhook
        with self.assertRaises(WebhookError):
            register_webhook(self.user.id, "http://127.0.0.1:8000/hook",
                             ["stage.added"])

    def test_webhook_registration_allows_public_host(self):
        from services.webhooks import register_webhook
        # example.test doesn't resolve in the sandbox -> allowed (fetch-time
        # failure is harmless); this also keeps offline dev working.
        rec = register_webhook(self.user.id, "https://example.test/hook",
                               ["stage.added"])
        self.assertTrue(rec.id)


if __name__ == "__main__":
    unittest.main()
