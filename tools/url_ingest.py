"""Optional job-URL ingestion.

Fetches a URL and returns cleaned posting text. For static pages a plain
``requests`` GET + HTML clean is enough. JS-heavy boards (LinkedIn / Indeed /
Glassdoor) return only a shell to ``requests``, so we route those — and any
page whose plain fetch comes back too thin — to a **headless browser**:

  1. The local Playwright scraper (``BROWSER_SCRAPER_ENABLED=1``; needs
     ``playwright install chromium``), or
  2. A **hosted** headless browser (Browserbase, ``BROWSERBASE_API_KEY`` +
     ``BROWSERBASE_PROJECT_ID``) — no local chromium, so this works on a
     vanilla deploy. Reuses ``tools.company_research.deep_fetch``.

When no browser backend is configured we degrade honestly (raise a clear,
actionable error naming the board and the paste alternative) rather than
silently substituting empty/consent-wall text.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from utils.env import env_bool, env_float, env_int

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; JobOfferScannerBot/0.2; +https://example.invalid/bot)"
)
_HTTP_TIMEOUT = env_float("URL_INGEST_TIMEOUT", 15.0)
_MAX_BYTES = env_int("URL_INGEST_MAX_BYTES", 1_500_000)  # ~1.5 MB

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Boards that render postings with JS — a plain GET returns a shell (or a
# consent wall), so we go straight to a real browser for these.
_JS_BOARD_HOSTS = (
    "linkedin.com",
    "indeed.",          # indeed.com, indeed.co.uk, …
    "glassdoor.",
    "ziprecruiter.com",
)


def is_url(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(_URL_RE.match(text.strip()))


def is_js_board(url: str) -> bool:
    """True for known JS-rendered boards that won't yield to a plain fetch."""
    low = (url or "").lower()
    return any(host in low for host in _JS_BOARD_HOSTS)


def browser_ingest_available() -> bool:
    """Whether any headless-browser backend is configured (local or hosted)."""
    if env_bool("BROWSER_SCRAPER_ENABLED"):
        return True
    return bool(os.getenv("BROWSERBASE_API_KEY") and os.getenv("BROWSERBASE_PROJECT_ID"))


def fetch_job_posting(url: str) -> str:
    """Fetch a URL and return cleaned, plain-text job posting content.

    Raises ``ValueError`` with a user-actionable message if the URL is invalid,
    the fetch fails, or the response is empty / too large / not HTML.
    """
    if not is_url(url):
        raise ValueError("URL must start with http:// or https://")

    # Known JS boards never yield to a plain GET — go straight to a browser so
    # we don't ingest a consent wall's boilerplate as if it were the posting.
    if is_js_board(url):
        browser_text = _try_browser_fallback(url)
        if browser_text and len(browser_text) >= 200:
            return browser_text
        raise ValueError(
            f"{url} is a JS-rendered board (LinkedIn / Indeed / Glassdoor) that "
            "needs a headless browser. Configure one (BROWSER_SCRAPER_ENABLED=1 "
            "with chromium, or BROWSERBASE_API_KEY + BROWSERBASE_PROJECT_ID), or "
            "paste the job description text instead."
        )

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
            timeout=_HTTP_TIMEOUT,
            stream=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"Failed to fetch {url}: {exc}") from exc

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type.lower():
        raise ValueError(
            f"Expected HTML at {url}, got Content-Type={content_type!r}. "
            "Paste the job description text instead."
        )

    body = resp.content[:_MAX_BYTES]
    text = _clean_html(body)
    if len(text) < 200:
        # Likely a JS-rendered shell. Try a headless browser if enabled.
        browser_text = _try_browser_fallback(url)
        if browser_text:
            return browser_text
        raise ValueError(
            f"Fetched page yielded only {len(text)} characters of text — "
            "likely a JS-rendered page (LinkedIn / Indeed / Glassdoor). "
            "Enable the browser scraper (BROWSER_SCRAPER_ENABLED=1) or paste "
            "the job description text instead."
        )
    return text


def _try_browser_fallback(url: str) -> Optional[str]:
    """Render ``url`` via the best available headless browser, or None.

    Order: the local Playwright scraper (if explicitly enabled) → a hosted
    Browserbase session (if configured). Both are optional; any failure
    degrades to None so the caller can surface the paste hint.
    """
    # 1. Local Playwright scraper (heavy; opt-in via BROWSER_SCRAPER_ENABLED).
    try:
        from tools.browser_scraper import browser_enabled, scrape_job_posting

        if browser_enabled():
            try:
                return scrape_job_posting(url)
            except Exception as exc:  # noqa: BLE001 - try the hosted path next
                logger.warning("Local browser fallback failed for %s: %s", url, exc)
    except ImportError:
        pass

    # 2. Hosted headless browser (Browserbase) — no local chromium required.
    try:
        from tools.company_research import deep_fetch

        text = deep_fetch(url)
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 - fall through to the paste hint
        logger.warning("Hosted browser fallback failed for %s: %s", url, exc)
    return None


def _clean_html(body: bytes) -> str:
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()

    # Prefer a likely job-description container if present.
    candidates = soup.find_all(
        ["article", "main", "section", "div"],
        attrs={"class": re.compile(r"(job|posting|description|content)", re.I)},
    )
    root = candidates[0] if candidates else soup

    text = root.get_text(separator="\n", strip=True)
    # Collapse runs of blank lines.
    return re.sub(r"\n{3,}", "\n\n", text)
