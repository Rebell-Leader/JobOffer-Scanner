"""P2 #10: JS-board ingestion via a headless browser (local or hosted).

Known JS boards route straight to a browser; the browser fallback now also
uses the hosted Browserbase path (no local chromium), so ingestion works on a
vanilla deploy. Network/browser are mocked.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class JsBoardDetectionTests(unittest.TestCase):
    def test_known_boards_detected(self):
        from tools.url_ingest import is_js_board
        for url in (
            "https://www.linkedin.com/jobs/view/123",
            "https://uk.indeed.com/viewjob?jk=abc",
            "https://www.glassdoor.com/job-listing/x",
            "https://www.ziprecruiter.com/jobs/x",
        ):
            self.assertTrue(is_js_board(url), url)

    def test_static_site_not_a_board(self):
        from tools.url_ingest import is_js_board
        self.assertFalse(is_js_board("https://example.com/careers/123"))
        self.assertFalse(is_js_board("https://boards.greenhouse.io/acme/jobs/1"))


class BrowserAvailabilityTests(unittest.TestCase):
    def test_local_flag_enables(self):
        from tools.url_ingest import browser_ingest_available
        with mock.patch.dict(os.environ, {"BROWSER_SCRAPER_ENABLED": "1"}):
            self.assertTrue(browser_ingest_available())

    def test_browserbase_keys_enable(self):
        from tools.url_ingest import browser_ingest_available
        with mock.patch.dict(os.environ, {
            "BROWSERBASE_API_KEY": "k", "BROWSERBASE_PROJECT_ID": "p",
        }):
            self.assertTrue(browser_ingest_available())

    def test_disabled_by_default(self):
        from tools.url_ingest import browser_ingest_available
        for k in ("BROWSER_SCRAPER_ENABLED", "BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"):
            os.environ.pop(k, None)
        self.assertFalse(browser_ingest_available())


class JsBoardRoutingTests(unittest.TestCase):
    def test_js_board_uses_browser_and_skips_plain_get(self):
        import tools.url_ingest as url_ingest
        with mock.patch.object(url_ingest, "_try_browser_fallback",
                               return_value="J" * 500) as fb, \
             mock.patch.object(url_ingest.requests, "get") as get:
            out = url_ingest.fetch_job_posting("https://www.linkedin.com/jobs/view/1")
        self.assertEqual(out, "J" * 500)
        fb.assert_called_once()
        get.assert_not_called()  # never wasted a plain GET on a JS board

    def test_js_board_without_browser_raises_targeted_error(self):
        import tools.url_ingest as url_ingest
        with mock.patch.object(url_ingest, "_try_browser_fallback", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                url_ingest.fetch_job_posting("https://www.indeed.com/viewjob?jk=1")
        self.assertIn("headless browser", str(ctx.exception))

    def test_js_board_short_browser_text_raises(self):
        import tools.url_ingest as url_ingest
        with mock.patch.object(url_ingest, "_try_browser_fallback", return_value="too short"):
            with self.assertRaises(ValueError):
                url_ingest.fetch_job_posting("https://www.glassdoor.com/job/1")


class BrowserFallbackOrderingTests(unittest.TestCase):
    def test_hosted_used_when_local_disabled(self):
        import tools.url_ingest as url_ingest
        # Local scraper disabled -> falls through to hosted deep_fetch.
        with mock.patch("tools.browser_scraper.browser_enabled", return_value=False), \
             mock.patch("tools.company_research.deep_fetch", return_value="HOSTED" * 50) as deep:
            out = url_ingest._try_browser_fallback("https://www.linkedin.com/jobs/1")
        self.assertTrue(out.startswith("HOSTED"))
        deep.assert_called_once()

    def test_local_preferred_when_enabled(self):
        import tools.url_ingest as url_ingest
        with mock.patch("tools.browser_scraper.browser_enabled", return_value=True), \
             mock.patch("tools.browser_scraper.scrape_job_posting",
                        return_value="LOCAL" * 50) as local, \
             mock.patch("tools.company_research.deep_fetch") as deep:
            out = url_ingest._try_browser_fallback("https://www.linkedin.com/jobs/1")
        self.assertTrue(out.startswith("LOCAL"))
        local.assert_called_once()
        deep.assert_not_called()  # local succeeded; hosted not attempted

    def test_local_failure_falls_through_to_hosted(self):
        import tools.url_ingest as url_ingest
        with mock.patch("tools.browser_scraper.browser_enabled", return_value=True), \
             mock.patch("tools.browser_scraper.scrape_job_posting",
                        side_effect=ValueError("login wall")), \
             mock.patch("tools.company_research.deep_fetch", return_value="HOSTED" * 50):
            out = url_ingest._try_browser_fallback("https://www.linkedin.com/jobs/1")
        self.assertTrue(out.startswith("HOSTED"))

    def test_none_when_nothing_configured(self):
        import tools.url_ingest as url_ingest
        with mock.patch("tools.browser_scraper.browser_enabled", return_value=False), \
             mock.patch("tools.company_research.deep_fetch", return_value=None):
            self.assertIsNone(url_ingest._try_browser_fallback("https://www.linkedin.com/jobs/1"))


if __name__ == "__main__":
    unittest.main()
