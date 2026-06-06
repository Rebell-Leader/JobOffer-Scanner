"""Unit tests for Phase 6: browser-scraper parsing, async runner, email.

These validate PARSING against realistic real-shaped fixtures — the part with
actual bug risk. Live connection is blocked by the sandbox host allowlist, so
network fetching itself is not exercised here (it's a thin GET/render).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ---------------------------------------------------------------------------
# Browser scraper — Numbeo COL HTML parsing (real-shaped fixture)
# ---------------------------------------------------------------------------

# Abbreviated but structurally faithful to a real Numbeo cost-of-living page:
# rows of <td>label</td><td class="priceValue">amount currency</td>.
NUMBEO_FIXTURE = """
<html><body>
<table class="data_wide_table">
  <tr><th>Restaurants</th><th>Edit</th></tr>
  <tr>
    <td>Meal, Inexpensive Restaurant</td>
    <td class="priceValue">15.00&nbsp;€</td>
  </tr>
  <tr>
    <td>Meal for 2 People, Mid-range Restaurant, Three-course</td>
    <td class="priceValue">70.00&nbsp;€</td>
  </tr>
  <tr><th>Rent Per Month</th><th>Edit</th></tr>
  <tr>
    <td>Apartment (1 bedroom) in City Centre</td>
    <td class="priceValue">1,250.00&nbsp;€</td>
  </tr>
  <tr>
    <td>Apartment (3 bedrooms) in City Centre</td>
    <td class="priceValue">2,400.00&nbsp;€</td>
  </tr>
</table>
</body></html>
"""


class NumbeoParseTests(unittest.TestCase):
    def test_parse_numbeo_extracts_known_items(self):
        from tools.browser_scraper import _parse_numbeo_html

        out = _parse_numbeo_html(NUMBEO_FIXTURE, "Berlin")
        self.assertIsNotNone(out)
        self.assertIn("Berlin", out)
        self.assertIn("Meal (inexpensive restaurant): 15.00", out)
        self.assertIn("1BR rent (city center) /mo: 1,250.00", out)
        self.assertIn("3BR rent (city center) /mo: 2,400.00", out)

    def test_parse_numbeo_returns_none_on_empty(self):
        from tools.browser_scraper import _parse_numbeo_html
        self.assertIsNone(_parse_numbeo_html("<html><body>nothing</body></html>", "X"))

    def test_price_parser_handles_us_and_eu_formats(self):
        from tools.browser_scraper import _parse_price

        self.assertEqual(_parse_price("1,234.56 €"), 1234.56)   # US grouping
        self.assertEqual(_parse_price("€1.234,56"), 1234.56)    # EU grouping
        self.assertEqual(_parse_price("15.00 €"), 15.00)
        self.assertEqual(_parse_price("2 400,00"), 2400.00)     # space group + EU dec
        self.assertIsNone(_parse_price("n/a"))


# ---------------------------------------------------------------------------
# Browser scraper — job HTML parsing + disabled-by-default behavior
# ---------------------------------------------------------------------------

JOB_FIXTURE = """
<html><head><script>var x=1;</script><style>.a{}</style></head>
<body>
  <nav>menu</nav>
  <div class="job-details">
    <h1>Staff Backend Engineer</h1>
    <p>We need someone strong in Python, Go, and Kubernetes.</p>
    <ul><li>Design distributed systems</li><li>Mentor engineers</li></ul>
  </div>
  <footer>copyright</footer>
</body></html>
"""


class JobHtmlParseTests(unittest.TestCase):
    def test_parse_job_html_strips_chrome(self):
        from tools.browser_scraper import _parse_job_html

        text = _parse_job_html(JOB_FIXTURE)
        self.assertIn("Staff Backend Engineer", text)
        self.assertIn("Kubernetes", text)
        self.assertNotIn("menu", text)
        self.assertNotIn("copyright", text)
        self.assertNotIn("var x", text)

    def test_render_disabled_by_default(self):
        from tools.browser_scraper import BrowserUnavailable, render_html

        os.environ.pop("BROWSER_SCRAPER_ENABLED", None)
        with self.assertRaises(BrowserUnavailable):
            render_html("https://example.com")

    def test_browser_enabled_flag(self):
        from tools.browser_scraper import browser_enabled

        os.environ.pop("BROWSER_SCRAPER_ENABLED", None)
        self.assertFalse(browser_enabled())
        with mock.patch.dict(os.environ, {"BROWSER_SCRAPER_ENABLED": "1"}):
            self.assertTrue(browser_enabled())


# ---------------------------------------------------------------------------
# url_ingest browser fallback wiring
# ---------------------------------------------------------------------------

class UrlIngestFallbackTests(unittest.TestCase):
    def test_short_page_triggers_browser_fallback_when_enabled(self):
        import tools.url_ingest as url_ingest

        class _Resp:
            status_code = 200
            headers = {"content-type": "text/html"}
            content = b"<html><body>tiny</body></html>"
            def raise_for_status(self): pass

        with mock.patch.object(url_ingest.requests, "get", return_value=_Resp()), \
             mock.patch.object(url_ingest, "_try_browser_fallback", return_value="X" * 500) as fb:
            out = url_ingest.fetch_job_posting("https://example.com/job/1")
        self.assertEqual(out, "X" * 500)
        fb.assert_called_once()

    def test_short_page_raises_when_fallback_unavailable(self):
        import tools.url_ingest as url_ingest

        class _Resp:
            status_code = 200
            headers = {"content-type": "text/html"}
            content = b"<html><body>tiny</body></html>"
            def raise_for_status(self): pass

        with mock.patch.object(url_ingest.requests, "get", return_value=_Resp()), \
             mock.patch.object(url_ingest, "_try_browser_fallback", return_value=None):
            with self.assertRaises(ValueError):
                url_ingest.fetch_job_posting("https://example.com/job/1")


# ---------------------------------------------------------------------------
# Async analysis runner (sync fallback path — no broker in tests)
# ---------------------------------------------------------------------------

class AnalysisRunnerTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("CELERY_BROKER_URL", None)

    def test_async_disabled_without_broker(self):
        # Reset the cached factory so it re-reads the (absent) broker env.
        from worker.celery_app import get_celery_app
        get_celery_app.cache_clear()
        from services import analysis_runner
        self.assertFalse(analysis_runner.async_enabled())

    def test_enqueue_returns_none_without_broker(self):
        from worker.celery_app import get_celery_app
        get_celery_app.cache_clear()
        from services import analysis_runner
        self.assertIsNone(
            analysis_runner.enqueue_analysis("posting", {"company_name": "X"})
        )

    def test_submit_runs_sync_when_no_broker(self):
        from services import analysis_runner

        fake = {"final_report": "# ok", "verdict": {}}
        with mock.patch.object(analysis_runner, "run_analysis_sync", return_value=fake) as run:
            out = analysis_runner.submit("posting text", {"company_name": "Acme"})
        self.assertEqual(out["mode"], "sync")
        self.assertEqual(out["result"], fake)
        run.assert_called_once()

    def test_analyze_payload_strips_callback(self):
        from worker import tasks

        fake = {"final_report": "x", "progress_callback": lambda *a: None}
        with mock.patch.object(tasks, "run_analysis", return_value=fake):
            out = tasks.analyze_payload("posting", {"company_name": "Acme"})
        self.assertNotIn("progress_callback", out)


# ---------------------------------------------------------------------------
# Email service
# ---------------------------------------------------------------------------

class EmailTests(unittest.TestCase):
    def setUp(self):
        for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM"):
            os.environ.pop(k, None)

    def test_not_configured_returns_false(self):
        from services.email import email_configured, send_email
        self.assertFalse(email_configured())
        self.assertFalse(send_email("a@b.com", "Subj", "Body"))

    def test_build_message(self):
        from services.email import build_message
        msg = build_message("a@b.com", "Subj", "Hello", sender="x@y.com")
        self.assertEqual(msg["To"], "a@b.com")
        self.assertEqual(msg["From"], "x@y.com")
        self.assertEqual(msg["Subject"], "Subj")
        self.assertIn("Hello", msg.get_content())

    def test_send_email_uses_smtp_when_configured(self):
        import services.email as email_mod

        env = {
            "SMTP_HOST": "smtp.test", "SMTP_PORT": "587",
            "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p",
            "EMAIL_FROM": "from@test.com", "SMTP_USE_TLS": "1",
        }
        fake_server = mock.MagicMock()
        smtp_ctx = mock.MagicMock()
        smtp_ctx.__enter__.return_value = fake_server
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(email_mod.smtplib, "SMTP", return_value=smtp_ctx):
            ok = email_mod.send_email("to@test.com", "S", "B")
        self.assertTrue(ok)
        fake_server.starttls.assert_called_once()
        fake_server.login.assert_called_once_with("u", "p")
        fake_server.send_message.assert_called_once()

    def test_reset_email_renders_link_when_base_url_set(self):
        import services.notifications as notif

        with mock.patch.dict(os.environ, {"APP_BASE_URL": "https://app.test"}), \
             mock.patch.object(notif, "send_email", return_value=True) as send:
            notif.send_password_reset_email("u@test.com", "TOK123")
        body = send.call_args[0][2]
        self.assertIn("https://app.test/?reset_token=TOK123", body)

    def test_reset_email_renders_token_without_base_url(self):
        import services.notifications as notif

        os.environ.pop("APP_BASE_URL", None)
        with mock.patch.object(notif, "send_email", return_value=False) as send:
            notif.send_password_reset_email("u@test.com", "TOK123")
        body = send.call_args[0][2]
        self.assertIn("TOK123", body)


if __name__ == "__main__":
    unittest.main()
