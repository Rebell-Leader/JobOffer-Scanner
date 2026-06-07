"""Agentic web-search fallback for company data.

When the paid/structured sources aren't configured (no ``NEWS_API_KEY`` for
news, no ``COL_DATASET_URL`` for cost-of-living), we still want *some* real,
current signal about the company rather than an empty sentinel. This module
provides a keyless, agentic fallback:

  1. The LLM proposes a few targeted search queries for the company.
  2. We run them against **DuckDuckGo** (no API key) — via the ``ddgs`` library
     if installed, else DuckDuckGo's HTML endpoint parsed with BeautifulSoup.
  3. Optionally a top result is deep-fetched with a **headless agentic
     browser** — Browserbase (hosted, ``BROWSERBASE_API_KEY``) or the local
     Playwright scraper (``BROWSER_SCRAPER_ENABLED``) — for JS-heavy pages.
  4. The LLM synthesises the snippets into a briefing, under the same
     no-fabrication rule as the rest of the app (use only what the snippets
     say; label inferences).

Everything degrades gracefully: no LLM key (demo mode) ⇒ skip entirely; no
search results ⇒ return ``None`` so the caller falls back to its sentinel.
Network/parse failures are swallowed and logged, never raised — a research
hiccup must never break the analysis pipeline.

Tunables (env):
  COMPANY_RESEARCH_FALLBACK   "0" to disable the whole fallback (default on)
  DDG_MAX_RESULTS             results per query (default 5)
  BROWSERBASE_API_KEY         enable hosted agentic-browser deep fetch
  BROWSERBASE_PROJECT_ID      required with the key
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import List, Optional

import requests

from utils.llm import get_active_provider, get_completion
from utils.security import wrap_untrusted

logger = logging.getLogger(__name__)

_DDG_HTML = "https://html.duckduckgo.com/html/"
_HTTP_TIMEOUT = float(os.getenv("RESEARCH_HTTP_TIMEOUT", "10"))
_USER_AGENT = (
    "Mozilla/5.0 (compatible; JobOfferScannerBot/0.2; +https://example.invalid/bot)"
)


def fallback_enabled() -> bool:
    """The agentic fallback needs an LLM (for queries + synthesis); it's off in
    demo mode and can be force-disabled."""
    if os.getenv("COMPANY_RESEARCH_FALLBACK", "1") != "1":
        return False
    return get_active_provider() is not None


# ---------------------------------------------------------------------------
# DuckDuckGo search (keyless)
# ---------------------------------------------------------------------------

def ddg_search(query: str, max_results: Optional[int] = None) -> List[dict]:
    """Return ``[{title, snippet, url}, …]`` for a query. Best-effort: [] on any
    failure. Prefers the ``ddgs``/``duckduckgo_search`` library, falls back to
    the HTML endpoint."""
    if not query:
        return []
    limit = max_results or int(os.getenv("DDG_MAX_RESULTS", "5"))

    lib_results = _ddg_via_library(query, limit)
    if lib_results:
        return lib_results
    return _ddg_via_html(query, limit)


def _load_ddgs():
    """Return the DDGS class from whichever package is installed, or None."""
    try:
        from ddgs import DDGS  # newer package name
        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS  # older name
        return DDGS
    except ImportError:
        return None


def _ddg_via_library(query: str, limit: int) -> List[dict]:
    ddgs_cls = _load_ddgs()
    if ddgs_cls is None:
        return []
    try:
        out: List[dict] = []
        with ddgs_cls() as ddgs:
            for r in ddgs.text(query, max_results=limit):
                out.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", "") or r.get("snippet", ""),
                    "url": r.get("href", "") or r.get("url", ""),
                })
        return out
    except Exception as exc:  # noqa: BLE001 - degrade to HTML / empty
        logger.warning("ddgs library search failed: %s", exc)
        return []


def _ddg_via_html(query: str, limit: int) -> List[dict]:
    try:
        resp = requests.post(
            _DDG_HTML,
            data={"q": query},
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return _parse_ddg_html(resp.text, limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DuckDuckGo HTML search failed for %r: %s", query, exc)
        return []


def _parse_ddg_html(html: str, limit: int) -> List[dict]:
    """Parse DuckDuckGo's HTML results page (pure)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: List[dict] = []
    for result in soup.select(".result")[: limit * 2]:
        a = result.select_one(".result__a")
        if a is None:
            continue
        snippet_el = result.select_one(".result__snippet")
        out.append({
            "title": a.get_text(strip=True),
            "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            "url": a.get("href", ""),
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Headless agentic browser deep-fetch (optional)
# ---------------------------------------------------------------------------

def deep_fetch(url: str) -> Optional[str]:
    """Fetch a URL's text via the best available browser backend, or None.

    Order: Browserbase (hosted, no local chromium) → local Playwright scraper
    → plain requests is intentionally NOT used here (the caller already has the
    DDG snippet; deep-fetch is only worth a real browser).
    """
    if not url:
        return None
    bb = _browserbase_fetch(url)
    if bb:
        return bb
    return _playwright_fetch(url)


def _browserbase_fetch(url: str) -> Optional[str]:
    """Render ``url`` via Browserbase's hosted headless browser (agentic-capable).

    Connects Playwright to a Browserbase session over CDP. Requires
    BROWSERBASE_API_KEY + BROWSERBASE_PROJECT_ID and the playwright package.
    Returns cleaned text or None.
    """
    api_key = os.getenv("BROWSERBASE_API_KEY")
    project_id = os.getenv("BROWSERBASE_PROJECT_ID")
    if not api_key or not project_id:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Browserbase configured but playwright not installed.")
        return None
    try:
        # Create a session, then connect Playwright to its CDP endpoint.
        sess = requests.post(
            "https://api.browserbase.com/v1/sessions",
            headers={"X-BB-API-Key": api_key, "Content-Type": "application/json"},
            json={"projectId": project_id},
            timeout=_HTTP_TIMEOUT,
        )
        sess.raise_for_status()
        connect_url = sess.json().get("connectUrl")
        if not connect_url:
            return None
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(connect_url)
            try:
                page = browser.contexts[0].pages[0] if browser.contexts else browser.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                html = page.content()
            finally:
                browser.close()
        from tools.browser_scraper import _parse_job_html  # reuse cleaner
        return _parse_job_html(html)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Browserbase deep-fetch failed for %s: %s", url, exc)
        return None


def _playwright_fetch(url: str) -> Optional[str]:
    try:
        from tools.browser_scraper import browser_enabled, scrape_job_posting
    except ImportError:
        return None
    if not browser_enabled():
        return None
    try:
        return scrape_job_posting(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Playwright deep-fetch failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Agentic orchestration
# ---------------------------------------------------------------------------

_FALLBACK_QUERIES = [
    "{company} company news",
    "{company} layoffs OR funding OR revenue",
    "{company} glassdoor reviews culture",
]


def build_queries(company: str, model: str = "fast") -> List[str]:
    """LLM-proposed search queries for the company; static fallback on failure."""
    static = [q.format(company=company) for q in _FALLBACK_QUERIES]
    if not company:
        return static
    prompt = (
        "Propose 3 concise web-search queries to research this company for a "
        "job seeker: stability/news, layoffs/financials, and culture. Return a "
        "STRICT JSON array of 3 strings, nothing else.\n\n"
        f"Company: {wrap_untrusted(company, 'company_name')}"
    )
    try:
        raw = get_completion(prompt, model).strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        queries = json.loads(raw)
        cleaned = [str(q).strip() for q in queries if str(q).strip()][:3]
        return cleaned or static
    except Exception as exc:  # noqa: BLE001 - static queries are fine
        logger.info("Query generation failed (%s); using static queries.", exc)
        return static


def _format_results(results: List[dict]) -> str:
    lines = []
    for r in results[:10]:
        title = (r.get("title") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        url = (r.get("url") or "").strip()
        if title or snippet:
            lines.append(f"- {title}\n  {snippet}\n  ({url})")
    return "\n".join(lines)


def agentic_company_research(company: str, model: str = "fast") -> Optional[str]:
    """Keyless agentic web research for a company. Returns a synthesised
    briefing string, or None when unavailable (caller uses its sentinel)."""
    if not fallback_enabled() or not company:
        return None

    queries = build_queries(company, model)
    results: List[dict] = []
    seen_urls = set()
    for q in queries:
        for r in ddg_search(q):
            u = r.get("url")
            if u and u in seen_urls:
                continue
            seen_urls.add(u)
            results.append(r)

    if not results:
        return None

    # Optional deep dive on the top result (only if a browser backend exists).
    deep = deep_fetch(results[0].get("url", ""))
    deep_block = (
        f"\n\nTop-result page content:\n{wrap_untrusted(deep[:4000], 'page')}"
        if deep else ""
    )

    prompt = f"""\
You are researching **{company}** for a job seeker using ONLY the web-search
results below. Do NOT invent facts not present in these snippets. Where the
snippets are thin, say so rather than guessing.

Search results:
{wrap_untrusted(_format_results(results), 'search_results')}{deep_block}

Produce a concise markdown briefing:
1. Recent news / signals (only what the snippets support)
2. Stability indicators (funding, layoffs, growth — only if mentioned)
3. Culture signals (only if mentioned; label as "Inference:" otherwise)
4. What the candidate should verify themselves

Start with: "REAL DATA (web search, DuckDuckGo) for {company}:".
Do not output <think> blocks or wrap the whole report in a code fence.
"""
    try:
        return get_completion(prompt, model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Company research synthesis failed: %s", exc)
        return None
