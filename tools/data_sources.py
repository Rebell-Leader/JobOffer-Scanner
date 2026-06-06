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
_ADZUNA_ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


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

# ---------------------------------------------------------------------------
# Cost-of-living dataset
# ---------------------------------------------------------------------------

def _format_col(record: dict, city: str) -> str:
    """Render a cost-of-living dataset record into a compact briefing."""

    def _fmt(key: str, label: str, unit: str = "") -> Optional[str]:
        val = record.get(key)
        if val is None:
            return None
        return f"- {label}: {val}{unit}"

    lines = [f"REAL DATA (cost-of-living dataset) for {city}:"]
    for key, label, unit in [
        ("cost_of_living_index", "Cost-of-living index (NYC=100)", ""),
        ("rent_index", "Rent index (NYC=100)", ""),
        ("groceries_index", "Groceries index", ""),
        ("local_purchasing_power", "Local purchasing power", ""),
        ("monthly_rent_1bedroom_city_center", "1BR rent (city center)", " /mo"),
        ("monthly_rent_3bedroom_city_center", "3BR rent (city center)", " /mo"),
        ("meal_inexpensive_restaurant", "Meal (inexpensive)", ""),
    ]:
        line = _fmt(key, label, unit)
        if line:
            lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else f"No COL record found for {city}."


def fetch_cost_of_living(city: str) -> Optional[str]:
    """Fetch a city's COL record from ``COL_DATASET_URL`` if configured.

    The dataset can be either a JSON list of records or an object with a
    ``cities`` array. Each record needs a ``city`` field; any of the keys in
    ``_format_col`` are surfaced when present. Honest fallback (``None``) when
    no dataset URL is set or the city isn't in the dataset.
    """
    url = os.getenv("COL_DATASET_URL")
    if not url or not city:
        return None
    try:
        resp = requests.get(url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        records = data if isinstance(data, list) else data.get("cities") or data.get("records") or []
        match = next(
            (r for r in records if city.lower() in str(r.get("city", "")).lower()),
            None,
        )
        if match is None:
            return None
        return _format_col(match, city)
    except Exception as exc:  # noqa: BLE001 - degrade to heuristic fallback
        logger.warning("COL fetch failed for %s: %s", city, exc)
        return None


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


# ---------------------------------------------------------------------------
# Salary benchmark (Adzuna)
# ---------------------------------------------------------------------------

_ADZUNA_DEFAULT_COUNTRY = "us"

# Adzuna covers a fixed list of country codes; map common location terms to
# them so a user typing "Berlin, Germany" hits the German feed.
_COUNTRY_HINTS = {
    "us": "us", "usa": "us", "united states": "us",
    "uk": "gb", "united kingdom": "gb", "england": "gb", "scotland": "gb",
    "germany": "de", "berlin": "de", "munich": "de", "deutschland": "de",
    "france": "fr", "paris": "fr",
    "netherlands": "nl", "amsterdam": "nl",
    "australia": "au", "sydney": "au", "melbourne": "au",
    "canada": "ca", "toronto": "ca", "vancouver": "ca",
    "poland": "pl", "warsaw": "pl",
    "india": "in", "bangalore": "in", "mumbai": "in",
    "singapore": "sg",
    "brazil": "br",
    "italy": "it", "rome": "it", "milan": "it",
    "spain": "es", "madrid": "es", "barcelona": "es",
    "austria": "at", "vienna": "at",
    "switzerland": "ch", "zurich": "ch",
    "new zealand": "nz",
    "mexico": "mx",
    "south africa": "za",
}


def _country_for_location(location: Optional[str]) -> str:
    if not location:
        return _ADZUNA_DEFAULT_COUNTRY
    loc = location.lower()
    for hint, code in _COUNTRY_HINTS.items():
        if hint in loc:
            return code
    return _ADZUNA_DEFAULT_COUNTRY


def _summarize_adzuna(payload: dict, job_title: str, location: str) -> Optional[str]:
    results = payload.get("results") or []
    salaries = [
        (r.get("salary_min"), r.get("salary_max"), r.get("salary_is_predicted"))
        for r in results
        if r.get("salary_min") and r.get("salary_max")
    ]
    if not salaries:
        return None
    lows = [s[0] for s in salaries]
    highs = [s[1] for s in salaries]
    medians = [(s[0] + s[1]) / 2 for s in salaries]
    currency = (results[0].get("salary_currency") or "").upper() or "local"
    predicted_share = sum(1 for s in salaries if str(s[2]) in ("1", "True", "true"))
    return (
        f"REAL DATA (Adzuna, {len(salaries)} matching postings for "
        f"'{job_title}' near '{location}'):\n"
        f"- Median: {round(sum(medians)/len(medians)):,} {currency}\n"
        f"- p10/p90 (approx): {min(lows):,} / {max(highs):,} {currency}\n"
        f"- Predicted salaries (Adzuna ML): {predicted_share}/{len(salaries)} postings\n"
        f"Treat as a market snapshot, not a personal offer benchmark."
    )


def fetch_salary_benchmark(
    job_title: str, location: Optional[str]
) -> Optional[str]:
    """Real salary benchmark via Adzuna when configured, else ``None``."""
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key or not job_title:
        return None

    country = _country_for_location(location)
    url = _ADZUNA_ENDPOINT.format(country=country)
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": job_title,
        "results_per_page": 50,
        "content-type": "application/json",
    }
    if location:
        params["where"] = location

    try:
        resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return _summarize_adzuna(resp.json(), job_title, location or country.upper())
    except Exception as exc:  # noqa: BLE001 - degrade to heuristic fallback
        logger.warning("Adzuna fetch failed for %s in %s: %s", job_title, location, exc)
        return None


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
