"""Pluggable external data sources for company research.

Each fetcher returns a formatted string when a real source is configured and
reachable, or ``None`` when it isn't — callers then fall back to an explicit
"NOT AVAILABLE" sentinel so the LLM never treats absence as good news.

Configured via env:
  * NEWS_API_KEY        -> newsapi.org company news
  * LAYOFFS_DATASET_URL -> JSON layoffs dataset (optional; see _fetch_layoffs)

This keeps the live path real while degrading honestly with no key/egress.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = float(os.getenv("DATA_SOURCE_TIMEOUT", "10"))
_NEWS_ENDPOINT = "https://newsapi.org/v2/everything"


# ---------------------------------------------------------------------------
# Company news (newsapi.org)
# ---------------------------------------------------------------------------

def _format_news(articles: list, company_name: str, limit: int = 5) -> str:
    """Format raw newsapi articles into a compact briefing string."""
    if not articles:
        return f"No recent news articles found for {company_name}."
    lines = [f"Recent news for {company_name} (source: newsapi.org):"]
    for art in articles[:limit]:
        title = (art.get("title") or "").strip()
        source = ((art.get("source") or {}).get("name") or "").strip()
        published = (art.get("publishedAt") or "")[:10]
        if title:
            lines.append(f"- [{published}] {title} ({source})")
    return "\n".join(lines)


def fetch_company_news(company_name: str) -> Optional[str]:
    """Fetch recent company news, or None if unavailable."""
    api_key = os.getenv("NEWS_API_KEY")
    if not api_key or not company_name:
        return None
    try:
        resp = requests.get(
            _NEWS_ENDPOINT,
            params={
                "q": f'"{company_name}"',
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": 10,
                "apiKey": api_key,
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            logger.warning("newsapi returned status=%s", data.get("status"))
            return None
        return _format_news(data.get("articles", []), company_name)
    except Exception as exc:  # noqa: BLE001 - degrade to fallback sentinel
        logger.warning("Company news fetch failed for %s: %s", company_name, exc)
        return None


# ---------------------------------------------------------------------------
# Layoffs (optional JSON dataset; layoffs.fyi has no official API)
# ---------------------------------------------------------------------------

def _format_layoffs(records: list, company_name: str) -> str:
    matches = [
        r for r in records
        if company_name.lower() in str(r.get("company", "")).lower()
    ]
    if not matches:
        return f"No layoff records found for {company_name} in the configured dataset."
    lines = [f"Layoff records for {company_name} (configured dataset):"]
    for r in matches[:5]:
        date = r.get("date", "?")
        count = r.get("laid_off") or r.get("count") or "?"
        lines.append(f"- {date}: {count} roles")
    return "\n".join(lines)


def fetch_layoffs(company_name: str) -> Optional[str]:
    """Fetch layoff records from a configured JSON dataset, or None."""
    url = os.getenv("LAYOFFS_DATASET_URL")
    if not url or not company_name:
        return None
    try:
        resp = requests.get(url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        records = data if isinstance(data, list) else data.get("records", [])
        return _format_layoffs(records, company_name)
    except Exception as exc:  # noqa: BLE001 - degrade to fallback sentinel
        logger.warning("Layoffs fetch failed for %s: %s", company_name, exc)
        return None
