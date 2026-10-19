"""Shared HTML → plain-text extraction for job postings.

Both the plain-fetch path (``tools.url_ingest``) and the headless-browser path
(``tools.browser_scraper``) need to turn posting HTML into clean text the same
way: drop chrome (script/style/nav/…), prefer a job-description-shaped
container, then collapse whitespace. This is that single implementation.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Iterator, Optional, Union

from bs4 import BeautifulSoup

# Chrome elements that never carry posting content.
_STRIP_TAGS = ("script", "style", "noscript", "header", "footer", "nav", "svg")
# Container classes that usually wrap the posting body.
_CONTENT_CLASS_RE = re.compile(r"(job|posting|description|content|details)", re.I)


def extract_job_text(html: Union[str, bytes]) -> str:
    """Return cleaned posting text from raw HTML (``str`` or ``bytes``).

    Prefers schema.org ``JobPosting`` JSON-LD, which Greenhouse, Lever, Ashby,
    Workday, SmartRecruiters and LinkedIn guest pages embed for Google Jobs:
    it is the complete posting, including salary, independent of page layout.
    (It lives in a ``<script>`` tag, which the DOM path below strips.)
    """
    soup = BeautifulSoup(html, "html.parser")
    structured = _job_posting_from_jsonld(soup)
    if structured:
        return structured

    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    candidates = soup.find_all(
        ["article", "main", "section", "div"],
        attrs={"class": _CONTENT_CLASS_RE},
    )
    # The longest match, not the first: the first is often a "job-header" or
    # "cookie-content" banner a few dozen characters long.
    root = (
        max(candidates, key=lambda c: len(c.get_text(strip=True))) if candidates else soup
    )
    text = root.get_text(separator="\n", strip=True)
    # Collapse runs of blank lines.
    return re.sub(r"\n{3,}", "\n\n", text)


# ---------------------------------------------------------------------------
# schema.org JobPosting (JSON-LD)
# ---------------------------------------------------------------------------

_MIN_JSONLD_DESCRIPTION = 200


def _iter_jsonld_nodes(data: Any) -> Iterator[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_nodes(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _iter_jsonld_nodes(data["@graph"])


def _is_job_posting(node: dict) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return "JobPosting" in kinds


def jobposting_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def jobposting_location(node: dict) -> str:
    places = node.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    out = []
    for place in places:
        addr = place.get("address") if isinstance(place, dict) else None
        if isinstance(addr, dict):
            parts = [
                jobposting_name(addr.get(k))
                for k in ("addressLocality", "addressRegion", "addressCountry")
            ]
            out.append(", ".join(p for p in parts if p))
        elif addr:
            out.append(str(addr))
    if str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        out.append("Remote")
    return "; ".join(dict.fromkeys(p for p in out if p))


def jobposting_salary(node: dict) -> str:
    base = node.get("baseSalary")
    if not isinstance(base, dict):
        return ""
    value = base.get("value")
    currency = base.get("currency") or ""
    if isinstance(value, dict):
        low, high = value.get("minValue"), value.get("maxValue")
        amount = f"{low}-{high}" if low and high else str(value.get("value") or low or high or "")
        unit = value.get("unitText") or ""
    else:
        amount, unit = str(value or ""), ""
    return " ".join(p for p in (currency, amount, f"per {unit.lower()}" if unit else "") if p)


def _job_posting_from_jsonld(soup: BeautifulSoup) -> Optional[str]:
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        try:
            data = json.loads(tag.get_text() or "")
        except ValueError:
            continue
        for node in _iter_jsonld_nodes(data):
            if not _is_job_posting(node):
                continue
            raw = html_lib.unescape(str(node.get("description") or ""))
            description = BeautifulSoup(raw, "html.parser").get_text(separator="\n", strip=True)
            if len(description) < _MIN_JSONLD_DESCRIPTION:
                continue
            employment = node.get("employmentType")
            if isinstance(employment, list):
                employment = ", ".join(map(str, employment))
            header = [
                ("Title", str(node.get("title") or "")),
                ("Company", jobposting_name(node.get("hiringOrganization"))),
                ("Location", jobposting_location(node)),
                ("Employment type", str(employment or "")),
                ("Compensation", jobposting_salary(node)),
                ("Date posted", str(node.get("datePosted") or "")),
            ]
            lines = [f"{label}: {value}" for label, value in header if value]
            return re.sub(r"\n{3,}", "\n\n", "\n".join(lines) + "\n\n" + description)
    return None
