"""P2 #13: reverse-proxy edge configs ship the hardening directives.

The Caddy/nginx examples are the source of truth for the web-UI security
headers Streamlit can't set itself. Guard that they don't silently lose a
directive, and that the deploy guide documents the topology.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
_CADDY = _DEPLOY / "Caddyfile.example"
_NGINX = _DEPLOY / "nginx.conf.example"
_README = _DEPLOY / "README.md"

# Header directives every public edge must carry for the web UI.
_REQUIRED = [
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "frame-ancestors 'none'",
]


class CaddyConfigTests(unittest.TestCase):
    def setUp(self):
        self.text = _CADDY.read_text()

    def test_has_required_headers(self):
        for d in _REQUIRED:
            self.assertIn(d, self.text, f"Caddyfile missing {d!r}")

    def test_routes_api_and_ui(self):
        self.assertIn("127.0.0.1:8000", self.text)  # API
        self.assertIn("127.0.0.1:5000", self.text)  # Streamlit UI

    def test_secure_cookie_upgrade(self):
        self.assertIn("Secure", self.text)


class NginxConfigTests(unittest.TestCase):
    def setUp(self):
        self.text = _NGINX.read_text()

    def test_has_required_headers(self):
        for d in _REQUIRED:
            self.assertIn(d, self.text, f"nginx conf missing {d!r}")

    def test_websocket_upgrade_wired(self):
        # Streamlit silently fails without the upgrade plumbing.
        self.assertIn("$connection_upgrade", self.text)
        self.assertIn("proxy_set_header Upgrade", self.text)

    def test_redirects_http_to_https(self):
        self.assertIn("return 301 https://", self.text)


class DeployReadmeTests(unittest.TestCase):
    def test_documents_topology_and_proxies(self):
        text = _README.read_text().lower()
        for needle in ("caddy", "nginx", "streamlit", "reverse proxy", "replit"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
