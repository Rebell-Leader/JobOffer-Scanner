"""Browser-based scraping for resources whose API is paid or unavailable.

Why this exists:
  * LinkedIn / Indeed / Glassdoor render postings with JS — a plain
    ``requests`` GET returns a shell. A real browser executes the JS.
  * Numbeo (cost-of-living) has no free API; the data lives in HTML tables.
  * layoffs.fyi exposes data through an Airtable embed, not an API.

Design:
  * The browser engine (Playwright) is imported lazily and is an OPTIONAL
    dependency. Nothing here imports Playwright at module load, so the rest of
    the app keeps working without it.
  * Rendering (network + browser) is split from PARSING (pure functions) so the
    parsers can be unit-tested against saved fixtures with no browser/network.
  * Disabled by default. Set ``BROWSER_SCRAPER_ENABLED=1`` to allow launching a
    headless browser (it's heavy; opt in explicitly).

NOTE: In a host-allowlisted sandbox the browser hits the same egress wall as
``requests`` — a 403 at the proxy. Browser rendering only helps where outbound
network is actually permitted.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from bs4 import BeautifulSoup

from utils.env import env_bool, env_int

logger = logging.getLogger(__name__)

_RENDER_TIMEOUT_MS = env_int("BROWSER_RENDER_TIMEOUT_MS", 30000)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def browser_enabled() -> bool:
    return env_bool("BROWSER_SCRAPER_ENABLED")


# ---------------------------------------------------------------------------
# Rendering (needs Playwright + network)
# ---------------------------------------------------------------------------

class BrowserUnavailable(RuntimeError):
    """Raised when browser rendering is requested but cannot run."""


def render_html(url: str, wait_selector: Optional[str] = None) -> str:
    """Render a URL in a headless browser and return the final HTML.

    Raises ``BrowserUnavailable`` if the scraper is disabled or Playwright is
    not installed, and ``ValueError`` for navigation failures.
    """
    if not browser_enabled():
        raise BrowserUnavailable(
            "Browser scraper disabled. Set BROWSER_SCRAPER_ENABLED=1 to enable."
        )
    try:
        from playwright.sync_api import sync_playwright  # lazy import
    except ImportError as exc:
        raise BrowserUnavailable(
            "Playwright not installed. `pip install playwright && playwright install chromium`."
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_USER_AGENT)
                page.goto(url, timeout=_RENDER_TIMEOUT_MS, wait_until="domcontentloaded")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=_RENDER_TIMEOUT_MS)
                    except Exception:  # noqa: BLE001 - selector optional
                        logger.warning("wait_selector %r not found; using current DOM.", wait_selector)
                return page.content()
            finally:
                browser.close()
    except BrowserUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - navigation/render failure
        raise ValueError(f"Browser render failed for {url}: {exc}") from exc


# ---------------------------------------------------------------------------
# Job posting (LinkedIn-class boards)
# ---------------------------------------------------------------------------

def scrape_job_posting(url: str) -> str:
    """Render a JS-heavy job page and return cleaned text."""
    html = render_html(url)
    text = _parse_job_html(html)
    if len(text) < 200:
        raise ValueError(
            f"Rendered page for {url} still yielded only {len(text)} characters — "
            "the board may require login. Paste the description text instead."
        )
    return text


def _parse_job_html(html: str) -> str:
    """Extract plain-text posting content from rendered HTML (pure)."""
    from tools.html_extract import extract_job_text
    return extract_job_text(html)


# ---------------------------------------------------------------------------
# Numbeo cost of living (paid API alternative)
# ---------------------------------------------------------------------------

_NUMBEO_COL_URL = "https://www.numbeo.com/cost-of-living/in/{city}"

# Map Numbeo line-item labels to our canonical COL fields.
_NUMBEO_ITEM_MAP = {
    "meal, inexpensive restaurant": "meal_inexpensive_restaurant",
    "apartment (1 bedroom) in city centre": "monthly_rent_1bedroom_city_center",
    "apartment (3 bedrooms) in city centre": "monthly_rent_3bedroom_city_center",
}


def scrape_numbeo_col(city: str) -> Optional[str]:
    """Render the Numbeo COL page for a city and parse it."""
    slug = city.strip().replace(" ", "-")
    html = render_html(_NUMBEO_COL_URL.format(city=slug), wait_selector="table")
    return _parse_numbeo_html(html, city)


def _parse_numbeo_html(html: str, city: str) -> Optional[str]:
    """Parse Numbeo COL HTML tables into a briefing string (pure).

    Numbeo lays each cost item out as a table row: a label cell followed by a
    price cell with class ``priceValue``. We pull the items we care about plus
    any indices Numbeo exposes.
    """
    soup = BeautifulSoup(html, "html.parser")
    items: Dict[str, float] = {}

    for row in soup.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        price_cell = row.find(class_="priceValue") or cells[1]
        price = _parse_price(price_cell.get_text(strip=True))
        if price is None:
            continue
        for needle, field in _NUMBEO_ITEM_MAP.items():
            if needle in label:
                items[field] = price

    if not items:
        return None

    lines = [f"REAL DATA (Numbeo, scraped) for {city}:"]
    labels = {
        "meal_inexpensive_restaurant": "Meal (inexpensive restaurant)",
        "monthly_rent_1bedroom_city_center": "1BR rent (city center) /mo",
        "monthly_rent_3bedroom_city_center": "3BR rent (city center) /mo",
    }
    for field, label in labels.items():
        if field in items:
            lines.append(f"- {label}: {items[field]:,.2f}")
    lines.append("Source: Numbeo (community-reported); treat as approximate.")
    return "\n".join(lines)


_PRICE_RE = re.compile(r"[\d][\d,. ]*")


def _parse_price(text: str) -> Optional[float]:
    """Extract a numeric price from a Numbeo cell like '1,234.56 €' or '€1.234,56'."""
    match = _PRICE_RE.search(text or "")
    if not match:
        return None
    raw = match.group(0).strip().replace(" ", "")
    # Heuristic: if both separators present, the last one is the decimal point.
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")  # european 1.234,56
        else:
            raw = raw.replace(",", "")                      # us 1,234.56
    elif "," in raw:
        # Lone comma: decimal if 1-2 trailing digits, else thousands sep.
        if re.search(r",\d{1,2}$", raw):
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None
