"""Job-discovery source connectors.

Every connector returns ``List[JobLead]`` (one normalized shape) or raises
``SourceError``. All of them use official public JSON APIs. Nothing here
scrapes a site against its terms; LinkedIn / Indeed stay paste-only.

Company boards (the "watchlist"; structured, complete, current):
  * Greenhouse      boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  * Lever           api.lever.co/v0/postings/{slug} (EU tenants: api.eu.lever.co)
  * Ashby           api.ashbyhq.com/posting-api/job-board/{slug}
  * SmartRecruiters api.smartrecruiters.com/v1/companies/{id}/postings

Aggregators (broad; each asks for a link back, which the feed shows):
  * Arbeitnow (EU, keyless)  * Remotive (remote; at most a few calls a day:
    cached 6h)  * RemoteOK (remote; credit the source)  * Hacker News
    "Who is hiring?" (monthly thread, via the Algolia HN API)
  * Adzuna (UK + EU countries; needs the free ADZUNA_APP_ID / ADZUNA_APP_KEY)

Responses are cached (utils.cache), so a feed refresh doesn't hammer the APIs.
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from utils.cache import cache
from utils.env import env_float

logger = logging.getLogger(__name__)

_TIMEOUT = env_float("JOB_SOURCE_TIMEOUT", 15.0)
_USER_AGENT = "JobOfferScanner/0.2 (personal job search; +https://github.com/Rebell-Leader/JobOffer-Scanner)"
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class SourceError(RuntimeError):
    """A source couldn't be read (network, unknown board, bad response)."""


@dataclass
class JobLead:
    source: str
    external_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    remote: Optional[bool] = None
    posted_at: Optional[datetime] = None
    salary: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JobLead":
        d = dict(d)
        if d.get("posted_at"):
            d["posted_at"] = datetime.fromisoformat(d["posted_at"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_json(url: str, params: Optional[dict] = None) -> Any:
    try:
        resp = requests.get(
            url, params=params, timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise SourceError(f"{url}: {exc}") from exc
    if resp.status_code == 404:
        raise SourceError(f"{url}: not found")
    if resp.status_code >= 400:
        raise SourceError(f"{url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(f"{url}: not JSON") from exc


def _cached(key: str, ttl_seconds: float, fetch: Callable[[], List[JobLead]]) -> List[JobLead]:
    """Per-source response cache with its own TTL (the global cache TTL is an
    upper bound). Stored as plain dicts so the Redis backend can hold it."""
    entry = cache.get(f"jobsrc:{key}")
    if isinstance(entry, dict) and time.time() - entry.get("t", 0) < ttl_seconds:
        return [JobLead.from_dict(d) for d in entry.get("v", [])]
    leads = fetch()
    cache.set(f"jobsrc:{key}", {"t": time.time(), "v": [lead.to_dict() for lead in leads]})
    return leads


_BLOCK_TAGS = [
    "p", "div", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
    "section", "article", "blockquote", "pre",
]


def html_to_text(raw: str) -> str:
    """Posting HTML (possibly entity-escaped, as Greenhouse sends it) → text.

    Line breaks only at block elements, so inline markup ("<b>Python</b>")
    stays inside its sentence; list items become bullets.
    """
    if not raw:
        return ""
    soup = BeautifulSoup(html_lib.unescape(raw), "html.parser")
    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in soup.find_all(_BLOCK_TAGS):
        if isinstance(tag, Tag):
            if tag.name == "li":
                tag.insert(0, NavigableString("• "))
            tag.append(NavigableString("\n"))
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in soup.get_text().splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _parse_dt(value: Any) -> Optional[datetime]:
    """ISO-8601 string or epoch seconds / milliseconds → naive UTC."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            ts = float(value)
            if ts > 1e11:  # milliseconds
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, OverflowError, OSError):
        return None


def _fix_mojibake(s: str) -> str:
    """RemoteOK double-encodes UTF-8 ("Sothebyâ\\x80\\x99s")."""
    if s and ("â" in s or "Ã" in s):
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def _check_slug(slug: str) -> str:
    slug = (slug or "").strip()
    if not _SLUG_RE.match(slug):
        raise SourceError(f"Invalid board slug {slug!r}.")
    return slug


def _looks_remote(*texts: str) -> Optional[bool]:
    joined = " ".join(t for t in texts if t).lower()
    if not joined:
        return None
    return True if re.search(r"\bremote\b|anywhere|worldwide|distributed", joined) else None


# ---------------------------------------------------------------------------
# Company boards
# ---------------------------------------------------------------------------

def greenhouse(slug: str, company: str = "") -> List[JobLead]:
    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true"})
        out = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(JobLead(
                source="greenhouse", external_id=str(j.get("id")),
                url=j.get("absolute_url", ""), title=j.get("title", ""),
                company=j.get("company_name") or company or slug, location=loc,
                remote=_looks_remote(loc),
                posted_at=_parse_dt(j.get("first_published") or j.get("updated_at")),
                description=html_to_text(j.get("content", "")),
                tags=[d.get("name", "") for d in j.get("departments") or [] if d.get("name")],
            ))
        return out

    return _cached(f"greenhouse:{slug.lower()}", 3600, fetch)


def _lever_salary(rng: Any) -> str:
    if not isinstance(rng, dict) or not (rng.get("min") or rng.get("max")):
        return ""
    interval = str(rng.get("interval") or "").replace("-", " ")
    return f"{rng.get('currency', '')} {rng.get('min', '')}-{rng.get('max', '')} {interval}".strip()


def lever(slug: str, company: str = "") -> List[JobLead]:
    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        data: Any = None
        for host in ("api.lever.co", "api.eu.lever.co"):  # EU tenants live on their own host
            try:
                data = _get_json(f"https://{host}/v0/postings/{slug}", {"mode": "json"})
            except SourceError:
                continue
            if isinstance(data, list) and data:
                break
        if not isinstance(data, list):
            raise SourceError(f"Lever board {slug!r} not found.")
        out = []
        for j in data:
            cats = j.get("categories") or {}
            loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
            parts = [j.get("descriptionPlain", "")]
            for section in j.get("lists") or []:
                parts.append(f"{section.get('text', '')}:\n{html_to_text(section.get('content', ''))}")
            parts.append(j.get("additionalPlain", ""))
            workplace = str(j.get("workplaceType") or "").lower()
            out.append(JobLead(
                source="lever", external_id=str(j.get("id")), url=j.get("hostedUrl", ""),
                title=j.get("text", ""), company=company or slug, location=loc,
                remote=True if workplace == "remote" else _looks_remote(loc),
                posted_at=_parse_dt(j.get("createdAt")),
                salary=_lever_salary(j.get("salaryRange")),
                description="\n\n".join(p for p in parts if p),
                tags=[t for t in (cats.get("team"), cats.get("commitment")) if t],
            ))
        return out

    return _cached(f"lever:{slug.lower()}", 3600, fetch)


def ashby(slug: str, company: str = "") -> List[JobLead]:
    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        data = _get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}", {"includeCompensation": "true"}
        )
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j.get("location", "")] + [
                s.get("location", "") for s in j.get("secondaryLocations") or []
            ]
            comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
            out.append(JobLead(
                source="ashby", external_id=str(j.get("id")), url=j.get("jobUrl", ""),
                title=j.get("title", ""), company=company or slug,
                location="; ".join(loc for loc in locs if loc),
                remote=bool(j.get("isRemote")) or None,
                posted_at=_parse_dt(j.get("publishedAt")), salary=comp,
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                tags=[t for t in (j.get("department"), j.get("employmentType")) if t],
            ))
        return out

    return _cached(f"ashby:{slug.lower()}", 3600, fetch)


def smartrecruiters(company_id: str, company: str = "") -> List[JobLead]:
    """The list endpoint has no description; deep analysis fetches the
    posting page (its JSON-LD carries the full text)."""
    company_id = _check_slug(company_id)

    def fetch() -> List[JobLead]:
        out: List[JobLead] = []
        for offset in (0, 100, 200):
            data = _get_json(
                f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings",
                {"limit": 100, "offset": offset},
            )
            for j in data.get("content", []):
                loc = j.get("location") or {}
                out.append(JobLead(
                    source="smartrecruiters", external_id=str(j.get("id")),
                    url=f"https://jobs.smartrecruiters.com/{company_id}/{j.get('id')}",
                    title=j.get("name", ""),
                    company=(j.get("company") or {}).get("name") or company or company_id,
                    location=loc.get("fullLocation") or ", ".join(
                        p for p in (loc.get("city"), loc.get("country")) if p
                    ),
                    remote=True if loc.get("remote") else None,
                    posted_at=_parse_dt(j.get("releasedDate")),
                    tags=[t for t in (
                        (j.get("experienceLevel") or {}).get("label"),
                        (j.get("typeOfEmployment") or {}).get("label"),
                    ) if t],
                ))
            if offset + 100 >= int(data.get("totalFound") or 0):
                break
        return out

    return _cached(f"smartrecruiters:{company_id.lower()}", 3600, fetch)


BOARD_FETCHERS: Dict[str, Callable[..., List[JobLead]]] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
}


def _slug_candidates(company: str) -> List[str]:
    base = re.sub(r"[^a-z0-9 ]", "", company.lower()).strip()
    words = base.split()
    cands = ["".join(words), "-".join(words), words[0] if words else ""]
    camel = "".join(w.capitalize() for w in re.sub(r"[^A-Za-z0-9 ]", "", company).split())
    return [c for c in dict.fromkeys(cands + [camel]) if c]


def detect_board(company: str) -> Optional[Tuple[str, str, int]]:
    """Find which public ATS board a company uses: (ats, slug, open_jobs)."""
    for slug in _slug_candidates(company):
        for ats in ("greenhouse", "lever", "ashby", "smartrecruiters"):
            try:
                jobs = BOARD_FETCHERS[ats](slug, company)
            except SourceError:
                continue
            if jobs:
                return ats, slug, len(jobs)
    return None


# ---------------------------------------------------------------------------
# Aggregators
# ---------------------------------------------------------------------------

def arbeitnow(pages: int = 2) -> List[JobLead]:
    def fetch() -> List[JobLead]:
        out = []
        for page in range(1, pages + 1):
            data = _get_json("https://www.arbeitnow.com/api/job-board-api", {"page": page})
            for j in data.get("data", []):
                out.append(JobLead(
                    source="arbeitnow", external_id=str(j.get("slug")), url=j.get("url", ""),
                    title=j.get("title", ""), company=j.get("company_name", ""),
                    location=j.get("location", ""), remote=bool(j.get("remote")) or None,
                    posted_at=_parse_dt(j.get("created_at")),
                    description=html_to_text(j.get("description", "")),
                    tags=list(j.get("tags") or []) + list(j.get("job_types") or []),
                ))
            if not (data.get("links") or {}).get("next"):
                break
        return out

    return _cached(f"arbeitnow:{pages}", 3600, fetch)


def remotive(search: str) -> List[JobLead]:
    search = (search or "").strip()[:80]

    def fetch() -> List[JobLead]:
        data = _get_json("https://remotive.com/api/remote-jobs", {"search": search, "limit": 100})
        return [
            JobLead(
                source="remotive", external_id=str(j.get("id")), url=j.get("url", ""),
                title=j.get("title", ""), company=j.get("company_name", ""),
                location=j.get("candidate_required_location", ""), remote=True,
                posted_at=_parse_dt(j.get("publication_date")), salary=j.get("salary") or "",
                description=html_to_text(j.get("description", "")),
                tags=list(j.get("tags") or [])[:10],
            )
            for j in data.get("jobs", [])
        ]

    # Remotive asks for only a few requests a day.
    return _cached(f"remotive:{search.lower()}", 6 * 3600, fetch)


def remoteok() -> List[JobLead]:
    def fetch() -> List[JobLead]:
        data = _get_json("https://remoteok.com/api")
        out = []
        for j in data if isinstance(data, list) else []:
            if not isinstance(j, dict) or not j.get("position"):
                continue  # element 0 is the API's legal notice
            lo, hi = j.get("salary_min") or 0, j.get("salary_max") or 0
            out.append(JobLead(
                source="remoteok", external_id=str(j.get("id")), url=j.get("url", ""),
                title=_fix_mojibake(j.get("position", "")), company=_fix_mojibake(j.get("company", "")),
                location=_fix_mojibake(j.get("location", "")) or "Remote", remote=True,
                posted_at=_parse_dt(j.get("epoch") or j.get("date")),
                salary=f"USD {lo}-{hi}" if lo and hi else "",
                description=html_to_text(_fix_mojibake(j.get("description", ""))),
                tags=list(j.get("tags") or []),
            ))
        return out

    return _cached("remoteok", 3600, fetch)


_ROLE_WORDS = re.compile(
    r"engineer|developer|scientist|designer|manager|lead|architect|analyst|devops|sre|"
    r"product|data|ml|ai|researcher|director|head|consultant|specialist",
    re.IGNORECASE,
)


_WORK_MODE_RE = re.compile(r"remote|onsite|on-site|hybrid|in-office|relocation", re.IGNORECASE)
_CITY_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:[ -][A-Z][a-zA-Z]+)*,\s*[A-Z]")


def _parse_hn_comment(c: dict) -> Optional[JobLead]:
    raw = c.get("text") or ""
    if not raw:
        return None
    header = html_to_text(raw.split("<p>", 1)[0]).replace("\n", " ").strip()
    parts = [p.strip() for p in re.split(r"\s+[|•–—]\s+|\s\|\s?|\|", header) if p.strip()]
    if not parts:
        return None
    company = re.sub(r"\s*\(.*?\)\s*", " ", parts[0]).strip()[:120]
    role = next((p for p in parts[1:] if _ROLE_WORDS.search(p)), "")
    # "PrairieLearn (Remote US) — ..." puts the location in the company's
    # parenthetical; otherwise it's a segment naming a work mode or a city.
    candidates = re.findall(r"\(([^)]*)\)", parts[0]) + parts[1:]
    locations = [
        p for p in candidates
        if _WORK_MODE_RE.search(p) or (_CITY_RE.search(p) and not _ROLE_WORDS.search(p))
    ]
    location = "; ".join(dict.fromkeys(locations[:3]))
    return JobLead(
        source="hn", external_id=str(c.get("id")),
        url=f"https://news.ycombinator.com/item?id={c.get('id')}",
        title=(role or header)[:200], company=company, location=location[:200],
        remote=_looks_remote(header), posted_at=_parse_dt(c.get("created_at")),
        description=html_to_text(raw),
    )


def hn_whos_hiring() -> List[JobLead]:
    """Top-level comments of the latest HN "Ask HN: Who is hiring?" thread."""

    def fetch() -> List[JobLead]:
        stories = _get_json(
            "https://hn.algolia.com/api/v1/search_by_date",
            {"tags": "story,author_whoishiring", "hitsPerPage": 6},
        ).get("hits", [])
        story = next((s for s in stories if "who is hiring" in (s.get("title") or "").lower()), None)
        if story is None:
            raise SourceError("No current 'Who is hiring?' thread found.")
        item = _get_json(f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
        leads = (_parse_hn_comment(c) for c in item.get("children") or [])
        return [lead for lead in leads if lead]

    return _cached("hn_whos_hiring", 6 * 3600, fetch)


# Adzuna country codes for the UK + EU markets it covers.
ADZUNA_COUNTRIES = ("gb", "de", "nl", "fr", "at", "be", "ch", "es", "it", "pl")


def adzuna_available() -> bool:
    return bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))


def adzuna(country: str, what: str, where: str = "") -> List[JobLead]:
    country = country.lower()
    if country not in ADZUNA_COUNTRIES:
        raise SourceError(f"Adzuna doesn't cover {country!r}.")
    if not adzuna_available():
        raise SourceError("Adzuna needs ADZUNA_APP_ID + ADZUNA_APP_KEY (free at developer.adzuna.com).")

    def fetch() -> List[JobLead]:
        params = {
            "app_id": os.getenv("ADZUNA_APP_ID"), "app_key": os.getenv("ADZUNA_APP_KEY"),
            "what": what, "results_per_page": 50, "max_days_old": 30,
            "content-type": "application/json",
        }
        if where:
            params["where"] = where
        data = _get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params)
        out = []
        for j in data.get("results", []):
            lo, hi = j.get("salary_min"), j.get("salary_max")
            out.append(JobLead(
                source="adzuna", external_id=str(j.get("id")), url=j.get("redirect_url", ""),
                title=j.get("title", ""), company=(j.get("company") or {}).get("display_name", ""),
                location=(j.get("location") or {}).get("display_name", ""),
                remote=_looks_remote(j.get("title", ""), j.get("description", "")),
                posted_at=_parse_dt(j.get("created")),
                salary=f"{int(lo)}-{int(hi)} (local currency)" if lo and hi else "",
                description=html_to_text(j.get("description", "")),
            ))
        return out

    return _cached(f"adzuna:{country}:{what.lower()}:{where.lower()}", 3600, fetch)


# Aggregator sources the feed can enable, with the attribution their terms ask for.
AGGREGATORS: Dict[str, dict] = {
    "arbeitnow": {"label": "Arbeitnow (EU)", "credit": "arbeitnow.com"},
    "remotive": {"label": "Remotive (remote)", "credit": "remotive.com"},
    "remoteok": {"label": "RemoteOK (remote)", "credit": "remoteok.com"},
    "hn": {"label": "HN Who is hiring", "credit": "news.ycombinator.com"},
    "adzuna": {"label": "Adzuna (UK/EU, needs key)", "credit": "adzuna.com"},
}
