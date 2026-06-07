"""P1: agentic web-search company-research fallback (mocked; always runs).

The live version is in tests/test_e2e_live.py (gated on RUN_E2E). Here we mock
DuckDuckGo + the LLM so the fallback logic, chain selection, and graceful
degradation are tested without network.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ---------------------------------------------------------------------------
# DuckDuckGo HTML parsing (pure)
# ---------------------------------------------------------------------------

_DDG_HTML = """
<html><body>
  <div class="result">
    <a class="result__a" href="https://news.example/acme-raises">Acme raises $60M Series B</a>
    <div class="result__snippet">Acme Technologies closed a $60M round led by Foo Ventures.</div>
  </div>
  <div class="result">
    <a class="result__a" href="https://glassdoor.example/acme">Acme reviews</a>
    <div class="result__snippet">Employees praise async culture; some cite fast pace.</div>
  </div>
</body></html>
"""


class DdgParseTests(unittest.TestCase):
    def test_parse_ddg_html_extracts_results(self):
        from tools.company_research import _parse_ddg_html

        out = _parse_ddg_html(_DDG_HTML, limit=5)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "Acme raises $60M Series B")
        self.assertIn("60M", out[0]["snippet"])
        self.assertTrue(out[0]["url"].startswith("https://news.example"))

    def test_parse_ddg_html_respects_limit(self):
        from tools.company_research import _parse_ddg_html

        self.assertEqual(len(_parse_ddg_html(_DDG_HTML, limit=1)), 1)

    def test_parse_empty_html(self):
        from tools.company_research import _parse_ddg_html

        self.assertEqual(_parse_ddg_html("<html></html>", limit=5), [])


class DdgSearchTests(unittest.TestCase):
    def test_html_fallback_used_when_library_absent(self):
        import tools.company_research as cr

        class _Resp:
            text = _DDG_HTML
            def raise_for_status(self):  # noqa: D401
                pass

        with mock.patch.object(cr, "_ddg_via_library", return_value=[]), \
             mock.patch.object(cr.requests, "post", return_value=_Resp()):
            results = cr.ddg_search("Acme news")
        self.assertEqual(len(results), 2)

    def test_library_results_preferred(self):
        import tools.company_research as cr

        lib = [{"title": "t", "snippet": "s", "url": "u"}]
        with mock.patch.object(cr, "_ddg_via_library", return_value=lib), \
             mock.patch.object(cr.requests, "post") as post:
            results = cr.ddg_search("Acme")
        self.assertEqual(results, lib)
        post.assert_not_called()  # HTML path skipped when library returns data

    def test_search_failure_returns_empty(self):
        import tools.company_research as cr

        with mock.patch.object(cr, "_ddg_via_library", return_value=[]), \
             mock.patch.object(cr.requests, "post", side_effect=cr.requests.ConnectionError("x")):
            self.assertEqual(cr.ddg_search("Acme"), [])

    def test_empty_query(self):
        from tools.company_research import ddg_search
        self.assertEqual(ddg_search(""), [])


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------

class BuildQueriesTests(unittest.TestCase):
    def test_llm_queries_parsed(self):
        import tools.company_research as cr

        with mock.patch.object(cr, "get_completion",
                               return_value='["Acme news 2026", "Acme layoffs", "Acme culture"]'):
            qs = cr.build_queries("Acme", "fast")
        self.assertEqual(qs, ["Acme news 2026", "Acme layoffs", "Acme culture"])

    def test_llm_code_fence_stripped(self):
        import tools.company_research as cr

        with mock.patch.object(cr, "get_completion",
                               return_value='```json\n["a","b","c"]\n```'):
            self.assertEqual(cr.build_queries("Acme"), ["a", "b", "c"])

    def test_static_fallback_on_bad_json(self):
        import tools.company_research as cr

        with mock.patch.object(cr, "get_completion", return_value="not json"):
            qs = cr.build_queries("Acme")
        self.assertTrue(all("Acme" in q for q in qs))
        self.assertEqual(len(qs), 3)


# ---------------------------------------------------------------------------
# Agentic orchestration
# ---------------------------------------------------------------------------

class AgenticResearchTests(unittest.TestCase):
    def test_disabled_in_demo_mode(self):
        import tools.company_research as cr

        # No provider -> demo -> fallback disabled -> None.
        with mock.patch.object(cr, "get_active_provider", return_value=None):
            self.assertIsNone(cr.agentic_company_research("Acme"))

    def test_force_disabled_via_env(self):
        import tools.company_research as cr

        with mock.patch.object(cr, "get_active_provider", return_value="openai"), \
             mock.patch.dict(os.environ, {"COMPANY_RESEARCH_FALLBACK": "0"}):
            self.assertIsNone(cr.agentic_company_research("Acme"))

    def test_no_results_returns_none(self):
        import tools.company_research as cr

        with mock.patch.object(cr, "get_active_provider", return_value="openai"), \
             mock.patch.object(cr, "build_queries", return_value=["q1"]), \
             mock.patch.object(cr, "ddg_search", return_value=[]):
            self.assertIsNone(cr.agentic_company_research("Acme"))

    def test_synthesises_briefing_from_results(self):
        import tools.company_research as cr

        results = [
            {"title": "Acme raises $60M", "snippet": "Series B", "url": "https://a"},
            {"title": "Acme culture", "snippet": "async", "url": "https://b"},
        ]
        captured = {}

        def fake_completion(prompt, model="fast"):
            captured["prompt"] = prompt
            return "REAL DATA (web search, DuckDuckGo) for Acme:\n- Raised $60M"

        with mock.patch.object(cr, "get_active_provider", return_value="openai"), \
             mock.patch.object(cr, "build_queries", return_value=["q"]), \
             mock.patch.object(cr, "ddg_search", return_value=results), \
             mock.patch.object(cr, "deep_fetch", return_value=None), \
             mock.patch.object(cr, "get_completion", side_effect=fake_completion):
            out = cr.agentic_company_research("Acme")

        self.assertIn("REAL DATA (web search", out)
        # Snippets + the no-fabrication framing made it into the synthesis prompt.
        self.assertIn("Acme raises $60M", captured["prompt"])
        self.assertIn("Do NOT invent", captured["prompt"])
        self.assertIn("<<<BEGIN_UNTRUSTED>>>", captured["prompt"])

    def test_deduplicates_urls_across_queries(self):
        import tools.company_research as cr

        dup = [{"title": "t", "snippet": "s", "url": "https://same"}]
        seen_prompts = []

        with mock.patch.object(cr, "get_active_provider", return_value="openai"), \
             mock.patch.object(cr, "build_queries", return_value=["q1", "q2"]), \
             mock.patch.object(cr, "ddg_search", return_value=dup), \
             mock.patch.object(cr, "deep_fetch", return_value=None), \
             mock.patch.object(cr, "get_completion",
                               side_effect=lambda p, model="fast": seen_prompts.append(p) or "ok"):
            cr.agentic_company_research("Acme")
        # The same URL from both queries should appear once in the prompt.
        self.assertEqual(seen_prompts[-1].count("https://same"), 1)


# ---------------------------------------------------------------------------
# deep_fetch backend selection
# ---------------------------------------------------------------------------

class DeepFetchTests(unittest.TestCase):
    def test_none_when_nothing_configured(self):
        import tools.company_research as cr

        for k in ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID", "BROWSER_SCRAPER_ENABLED"):
            os.environ.pop(k, None)
        self.assertIsNone(cr.deep_fetch("https://example.com"))

    def test_empty_url(self):
        import tools.company_research as cr
        self.assertIsNone(cr.deep_fetch(""))

    def test_browserbase_skipped_without_keys(self):
        import tools.company_research as cr

        for k in ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"):
            os.environ.pop(k, None)
        self.assertIsNone(cr._browserbase_fetch("https://example.com"))


# ---------------------------------------------------------------------------
# Fallback wiring in fetch_company_news
# ---------------------------------------------------------------------------

class NewsFallbackChainTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("NEWS_API_KEY", None)

    def test_falls_back_to_agentic_when_no_news_key(self):
        import tools.data_sources as ds

        with mock.patch("tools.company_research.agentic_company_research",
                        return_value="REAL DATA (web search) for Acme: ...") as agentic:
            out = ds.fetch_company_news("Acme")
        agentic.assert_called_once_with("Acme")
        self.assertIn("REAL DATA", out)

    def test_returns_none_when_agentic_also_unavailable(self):
        import tools.data_sources as ds

        with mock.patch("tools.company_research.agentic_company_research", return_value=None):
            self.assertIsNone(ds.fetch_company_news("Acme"))

    def test_newsapi_used_when_key_present(self):
        import tools.data_sources as ds

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"status": "ok", "articles": [
                    {"title": "Acme news", "source": {"name": "Wire"},
                     "publishedAt": "2026-05-01T00:00:00Z"}]}

        with mock.patch.dict(os.environ, {"NEWS_API_KEY": "k"}), \
             mock.patch.object(ds.requests, "get", return_value=_Resp()), \
             mock.patch("tools.company_research.agentic_company_research") as agentic:
            out = ds.fetch_company_news("Acme")
        self.assertIn("Acme news", out)
        agentic.assert_not_called()  # tier-1 succeeded, no fallback

    def test_newsapi_failure_falls_through_to_agentic(self):
        import tools.data_sources as ds

        with mock.patch.dict(os.environ, {"NEWS_API_KEY": "k"}), \
             mock.patch.object(ds.requests, "get",
                               side_effect=ds.requests.ConnectionError("down")), \
             mock.patch("tools.company_research.agentic_company_research",
                        return_value="web fallback") as agentic:
            out = ds.fetch_company_news("Acme")
        agentic.assert_called_once()
        self.assertEqual(out, "web fallback")


if __name__ == "__main__":
    unittest.main()
