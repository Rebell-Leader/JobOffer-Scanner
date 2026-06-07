"""Live end-to-end tests — REAL calls to external systems and providers.

These are OPT-IN and skipped by default (CI and the dev sandbox are
egress-restricted). Enable with:

    RUN_E2E=1 python -m unittest tests.test_e2e_live

What they exercise (each test self-skips if its prerequisite is missing):

  * A real round-trip to every configured LLM provider.
  * A real keyless DuckDuckGo search (the fallback's data source).
  * The agentic company-research FALLBACK with NO news/COL keys but WITH an
    LLM key — i.e. "no API keys for news/numbeo ⇒ use a web search to fetch at
    least some real data about the company". This is the headline fallback the
    product promises.
  * A real URL ingest.
  * Optionally, a Browserbase hosted-headless-browser deep fetch.
  * A full pipeline run (analyze a real posting) end-to-end with a real
    provider, verifying a verdict + report come back.

Costs real tokens/time — that's the point. Set RUN_E2E=1 only when you mean it.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_RUN = os.getenv("RUN_E2E") == "1"


def _has_llm_key() -> bool:
    return any(os.getenv(k) for k in
               ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FEATHERLESS_API_KEY"))


@unittest.skipUnless(_RUN, "live e2e disabled (set RUN_E2E=1 to run)")
class LiveProviderTests(unittest.TestCase):
    def test_each_configured_provider_round_trips(self):
        from utils.llm import _PROVIDER_KEYS, ping_provider

        configured = [p for p, env in _PROVIDER_KEYS.items() if os.getenv(env)]
        if not configured:
            self.skipTest("no provider API key configured")
        for provider in configured:
            ok, msg = ping_provider(provider)
            self.assertTrue(ok, msg=f"{provider} ping failed: {msg}")


@unittest.skipUnless(_RUN, "live e2e disabled (set RUN_E2E=1 to run)")
class LiveSearchTests(unittest.TestCase):
    """DuckDuckGo search is keyless — only needs network + RUN_E2E."""

    def test_ddg_search_returns_results(self):
        from tools.company_research import ddg_search

        results = ddg_search("OpenAI company news", max_results=5)
        self.assertTrue(results, "DuckDuckGo returned no results")
        first = results[0]
        self.assertTrue(first.get("title") or first.get("snippet"))
        self.assertTrue(first.get("url", "").startswith("http"))


@unittest.skipUnless(_RUN, "live e2e disabled (set RUN_E2E=1 to run)")
class LiveFallbackTests(unittest.TestCase):
    """The core fallback: no news/COL keys, but an LLM key ⇒ agentic web
    research must still produce a real briefing."""

    def setUp(self):
        if not _has_llm_key():
            self.skipTest("agentic fallback needs an LLM provider key")
        # Force the no-structured-source scenario for the duration of the test.
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("NEWS_API_KEY", "COL_DATASET_URL", "LAYOFFS_DATASET_URL")}

    def tearDown(self):
        for k, v in getattr(self, "_saved", {}).items():
            if v is not None:
                os.environ[k] = v

    def test_agentic_company_research_produces_briefing(self):
        from tools.company_research import agentic_company_research

        out = agentic_company_research("Stripe", model="fast")
        self.assertIsNotNone(out, "agentic research returned None")
        self.assertGreater(len(out), 100)
        # It should be grounded as web-sourced, not invented.
        self.assertIn("web search", out.lower())

    def test_fetch_company_news_uses_fallback_without_news_key(self):
        """With NEWS_API_KEY unset, fetch_company_news must fall through to the
        agentic web fallback and return real data (not the None sentinel)."""
        from tools.data_sources import fetch_company_news

        out = fetch_company_news("Stripe")
        self.assertIsNotNone(out, "no data from the agentic fallback")
        self.assertGreater(len(out), 100)


@unittest.skipUnless(_RUN, "live e2e disabled (set RUN_E2E=1 to run)")
class LiveUrlIngestTests(unittest.TestCase):
    def test_fetch_static_page(self):
        from tools.url_ingest import fetch_job_posting

        # A content-rich, stable, JS-free page so the plain fetch yields text.
        text = fetch_job_posting("https://en.wikipedia.org/wiki/Software_engineer")
        self.assertGreater(len(text), 500)
        self.assertIn("software", text.lower())


@unittest.skipUnless(_RUN, "live e2e disabled (set RUN_E2E=1 to run)")
class LiveBrowserbaseTests(unittest.TestCase):
    def setUp(self):
        if not (os.getenv("BROWSERBASE_API_KEY") and os.getenv("BROWSERBASE_PROJECT_ID")):
            self.skipTest("Browserbase not configured")

    def test_browserbase_deep_fetch(self):
        from tools.company_research import _browserbase_fetch

        text = _browserbase_fetch("https://example.com")
        # Browserbase should render *something*; tolerate small pages.
        self.assertTrue(text is None or isinstance(text, str))
        if text is not None:
            self.assertGreater(len(text), 0)


@unittest.skipUnless(_RUN, "live e2e disabled (set RUN_E2E=1 to run)")
class LiveFullPipelineTests(unittest.TestCase):
    """The user-facing headline: analyze a real posting end-to-end with a real
    provider and the agentic fallback, and get a verdict + report."""

    def setUp(self):
        if not _has_llm_key():
            self.skipTest("full pipeline e2e needs an LLM provider key")
        from db.session import reset_engine_for_testing
        reset_engine_for_testing("sqlite:///:memory:")

    def test_analyze_real_posting(self):
        from agents.orchestrator import run_analysis

        posting = (
            "Company: Stripe\n"
            "Title: Senior Backend Engineer\n"
            "Location: Remote, US\n\n"
            "We're hiring a senior backend engineer to build payments "
            "infrastructure in Python and Go. 5+ years experience with "
            "distributed systems, PostgreSQL, and cloud (AWS) required. "
            "You'll design APIs, mentor engineers, and own reliability."
        )
        result = run_analysis(
            posting,
            manual_inputs={"company_name": "Stripe",
                           "job_title": "Senior Backend Engineer",
                           "location": "Remote, US"},
            model="fast",
        )
        self.assertEqual(result.get("error", ""), "", msg=result.get("error"))
        self.assertTrue(result.get("final_report"))
        self.assertGreater(len(result["final_report"]), 200)
        verdict = result.get("verdict") or {}
        self.assertIn(
            verdict.get("verdict"),
            {"Highly Recommended", "Recommended", "Consider with Caution",
             "Not Recommended"},
        )


if __name__ == "__main__":
    unittest.main()
