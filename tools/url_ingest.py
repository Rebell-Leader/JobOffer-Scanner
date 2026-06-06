"""Optional job-URL ingestion.

Fetches a URL and returns cleaned posting text. This is a best-effort generic
scraper — LinkedIn / Indeed / Glassdoor often require auth or render with JS
and will fail; we degrade honestly (raise a clear error) rather than silently
substitute empty text.

A site-specific scraper (Playwright/Selenium for JS-heavy pages) is the next
upgrade if URL ingest becomes a primary entry point.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; JobOfferScannerBot/0.2; +https://example.invalid/bot)"
)
_HTTP_TIMEOUT = float(os.getenv("URL_INGEST_TIMEOUT", "15"))
_MAX_BYTES = int(os.getenv("URL_INGEST_MAX_BYTES", "1500000"))  # ~1.5 MB

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_url(text: Optional[str]) -> bool:
    return bool(text) and bool(_URL_RE.match(text.strip()))


def fetch_job_posting(url: str) -> str:
    """Fetch a URL and return cleaned, plain-text job posting content.

    Raises ``ValueError`` with a user-actionable message if the URL is invalid,
    the fetch fails, or the response is empty / too large / not HTML.
    """
    if not is_url(url):
        raise ValueError("URL must start with http:// or https://")

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
        raise ValueError(
            f"Fetched page yielded only {len(text)} characters of text — "
            "likely a JS-rendered page (LinkedIn / Indeed / Glassdoor). "
            "Paste the job description text instead."
        )
    return text


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
