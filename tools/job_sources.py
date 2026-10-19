"""Job-discovery source connectors.

Every connector returns ``List[JobLead]`` (one normalized shape) or raises
``SourceError``. All of them use official public JSON APIs. Nothing here
scrapes a site against its terms; LinkedIn / Indeed stay paste-only.

Company boards (the "watchlist"; structured, complete, current):
  * Greenhouse      boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  * Lever           api.lever.co/v0/postings/{slug} (EU tenants: api.eu.lever.co)
  * Ashby           api.ashbyhq.com/posting-api/job-board/{slug}
  * SmartRecruiters api.smartrecruiters.com/v1/companies/{id}/postings
  * Personio        {slug}.jobs.personio.de/xml (dominant DACH startup ATS)
  * Teamtailor      {careers host}/jobs.rss (documented public feed)
  * Recruitee       {slug}.recruitee.com/api/offers/ (has salary)
  * Workable        apply.workable.com/api/v1/widget/accounts/{slug}?details=true
  * Pinpoint        {slug}.pinpointhq.com/postings.json (has compensation)
  * Softgarden      {slug}.career.softgarden.de/jobs.feed.json (schema.org feed)
  See docs/JOB_SOURCES.md for terms and the sources deliberately not used.

Aggregators (broad; each asks for a link back, which the feed shows):
  * Arbeitnow (EU, keyless)  * Remotive (remote; at most a few calls a day:
    cached 6h)  * RemoteOK (remote; credit the source)  * Hacker News
    "Who is hiring?" (monthly thread, via the Algolia HN API)
  * Adzuna (UK + EU countries; needs the free ADZUNA_APP_ID / ADZUNA_APP_KEY)
  * GermanTechJobs / SwissDevJobs / DevITjobs.uk RSS (salary on every item)

Responses are cached (utils.cache), so a feed refresh doesn't hammer the APIs.
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from defusedxml import ElementTree as SafeET

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


def _get_xml(url: str, params: Optional[dict] = None) -> Any:
    """Fetch and parse an XML/RSS feed. Parsed with defusedxml: the feeds are
    third-party content, so entity-expansion and external-entity tricks are
    rejected rather than processed."""
    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT})
    except requests.RequestException as exc:
        raise SourceError(f"{url}: {exc}") from exc
    if resp.status_code >= 400:
        raise SourceError(f"{url}: HTTP {resp.status_code}")
    try:
        return SafeET.fromstring(resp.content)
    except Exception as exc:  # noqa: BLE001 - ParseError / DefusedXmlException
        raise SourceError(f"{url}: not a valid feed ({exc})") from exc


def _xml_text(node: Any, path: str, ns: Optional[dict] = None) -> str:
    found = node.find(path, ns) if node is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def _rss_date(value: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return _parse_dt(value)
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


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


def personio(slug: str, company: str = "") -> List[JobLead]:
    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        root = _get_xml(f"https://{slug}.jobs.personio.de/xml", {"language": "en"})
        out = []
        for pos in root.iter("position"):
            pid = _xml_text(pos, "id")
            offices = [_xml_text(pos, "office")] + [
                (o.text or "").strip() for o in pos.findall("additionalOffices/office")
            ]
            sections = [
                f"{_xml_text(d, 'name')}:\n{html_to_text(_xml_text(d, 'value'))}"
                for d in pos.findall("jobDescriptions/jobDescription")
            ]
            location = ", ".join(o for o in dict.fromkeys(offices) if o)
            out.append(JobLead(
                source="personio", external_id=pid,
                url=f"https://{slug}.jobs.personio.de/job/{pid}?language=en",
                title=_xml_text(pos, "name"),
                company=_xml_text(pos, "subcompany") or company or slug,
                location=location, remote=_looks_remote(location),
                posted_at=_parse_dt(_xml_text(pos, "createdAt")),
                description="\n\n".join(sections),
                tags=[t for t in (_xml_text(pos, "department"), _xml_text(pos, "seniority"),
                                  _xml_text(pos, "schedule")) if t],
            ))
        return out

    return _cached(f"personio:{slug.lower()}", 3600, fetch)


_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_TT_NS = {"tt": "https://teamtailor.com/locations"}


def teamtailor(host: str, company: str = "") -> List[JobLead]:
    """``host`` is the careers site: ``acme.teamtailor.com`` or a custom
    domain (``careers.acme.com``); a bare slug means ``{slug}.teamtailor.com``."""
    host = (host or "").strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if "." not in host:
        host = f"{_check_slug(host)}.teamtailor.com"
    if not _HOST_RE.match(host):
        raise SourceError(f"Invalid careers host {host!r}.")
    from utils.security import check_url_allowed

    ok, reason = check_url_allowed(f"https://{host}/")  # custom domains are user input
    if not ok:
        raise SourceError(reason)

    def fetch() -> List[JobLead]:
        out: List[JobLead] = []
        for offset in (0, 100, 200):
            channel = _get_xml(
                f"https://{host}/jobs.rss", {"offset": offset, "per_page": 100}
            ).find("channel")
            items = channel.findall("item") if channel is not None else []
            name = company or _xml_text(channel, "title") or host.split(".")[0]
            for it in items:
                places = [
                    ", ".join(p for p in (_xml_text(loc, "tt:city", _TT_NS),
                                          _xml_text(loc, "tt:country", _TT_NS)) if p)
                    for loc in it.findall("tt:locations/tt:location", _TT_NS)
                ]
                status = _xml_text(it, "remoteStatus").lower()
                out.append(JobLead(
                    source="teamtailor", external_id=_xml_text(it, "guid"), url=_xml_text(it, "link"),
                    title=_xml_text(it, "title"), company=name,
                    location="; ".join(p for p in places if p) or ("Remote" if status == "fully" else ""),
                    remote=True if status in ("fully", "remote") else None,
                    posted_at=_rss_date(_xml_text(it, "pubDate")),
                    description=html_to_text(_xml_text(it, "description")),
                    tags=[t for t in (_xml_text(it, "tt:department", _TT_NS),
                                      _xml_text(it, "tt:role", _TT_NS), status) if t],
                ))
            if len(items) < 100:
                break
        return out

    return _cached(f"teamtailor:{host}", 3600, fetch)


def _range(lo: Any, hi: Any, currency: Any, period: Any = "") -> str:
    if not (lo or hi):
        return ""
    amount = f"{lo}-{hi}" if lo and hi else str(lo or hi)
    return " ".join(p for p in (str(currency or ""), amount, f"per {period}" if period else "") if p)


def recruitee(slug: str, company: str = "") -> List[JobLead]:
    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        data = _get_json(f"https://{slug}.recruitee.com/api/offers/")
        out = []
        for o in data.get("offers", []):
            sal = o.get("salary") or {}
            out.append(JobLead(
                source="recruitee", external_id=str(o.get("id")), url=o.get("careers_url", ""),
                title=o.get("title", ""), company=o.get("company_name") or company or slug,
                location=o.get("location") or ", ".join(p for p in (o.get("city"), o.get("country")) if p),
                remote=True if o.get("remote") else None,
                posted_at=_parse_dt(str(o.get("published_at") or "").replace(" UTC", "+00:00").replace(" ", "T", 1)),
                salary=_range(sal.get("min"), sal.get("max"), sal.get("currency"), sal.get("period")),
                description="\n\n".join(
                    html_to_text(o.get(k) or "") for k in ("description", "requirements") if o.get(k)
                ),
                tags=[t for t in (o.get("department"), "hybrid" if o.get("hybrid") else "") if t],
            ))
        return out

    return _cached(f"recruitee:{slug.lower()}", 3600, fetch)


def workable(slug: str, company: str = "") -> List[JobLead]:
    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        data = _get_json(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}", {"details": "true"}
        )
        out = []
        for j in data.get("jobs", []):
            places = [
                ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
                for loc in j.get("locations") or []
            ] or [", ".join(p for p in (j.get("city"), j.get("country")) if p)]
            remote = j.get("telecommuting") in (True, "true", "True")
            out.append(JobLead(
                source="workable", external_id=str(j.get("shortcode")), url=j.get("url", ""),
                title=j.get("title", ""), company=data.get("name") or company or slug,
                location="; ".join(dict.fromkeys(p for p in places if p)),
                remote=True if remote else _looks_remote(j.get("title", "")),
                posted_at=_parse_dt(j.get("published_on") or j.get("created_at")),
                description=html_to_text(j.get("description", "")),
                tags=[t for t in (j.get("department"), j.get("function"), j.get("employment_type")) if t],
            ))
        return out

    return _cached(f"workable:{slug.lower()}", 3600, fetch)


def pinpoint(slug: str, company: str = "") -> List[JobLead]:
    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        data = _get_json(f"https://{slug}.pinpointhq.com/postings.json")
        out = []
        for j in data.get("data", []):
            loc = j.get("location") or {}
            workplace = str(j.get("workplace_type") or "").lower()
            parts = [j.get("description") or ""] + [
                f"{j.get(k + '_header') or ''}\n{j.get(k) or ''}"
                for k in ("key_responsibilities", "skills_knowledge_expertise", "benefits") if j.get(k)
            ]
            out.append(JobLead(
                source="pinpoint", external_id=str(j.get("id")), url=j.get("url", ""),
                title=j.get("title", ""), company=company or slug,
                location=", ".join(dict.fromkeys(p for p in (loc.get("city"), loc.get("name")) if p)),
                remote=True if workplace == "remote" else None,
                salary=_range(j.get("compensation_minimum"), j.get("compensation_maximum"),
                              j.get("compensation_currency"), j.get("compensation_frequency"))
                if j.get("compensation_visible") else "",
                description="\n\n".join(html_to_text(p) for p in parts if p.strip()),
                tags=[t for t in (((j.get("job") or {}).get("department") or {}).get("name"),
                                  j.get("employment_type_text"), workplace) if t],
            ))
        return out

    return _cached(f"pinpoint:{slug.lower()}", 3600, fetch)


def softgarden(slug: str, company: str = "") -> List[JobLead]:
    from tools.html_extract import jobposting_location, jobposting_name, jobposting_salary

    slug = _check_slug(slug)

    def fetch() -> List[JobLead]:
        data = _get_json(f"https://{slug}.career.softgarden.de/jobs.feed.json")
        out = []
        for element in data.get("dataFeedElement", []):
            node = element.get("item") or {}
            loc = jobposting_location(node)
            ident = node.get("identifier") or {}
            out.append(JobLead(
                source="softgarden", external_id=str(ident.get("value") or node.get("url", "")),
                url=node.get("url", ""), title=node.get("title", ""),
                company=jobposting_name(node.get("hiringOrganization")) or str(ident.get("name") or "")
                or company or slug,
                location=loc, remote=_looks_remote(loc),
                posted_at=_parse_dt(node.get("datePosted")), salary=jobposting_salary(node),
                description=html_to_text(node.get("description", "")),
                tags=[str(node.get("employmentType") or "")] if node.get("employmentType") else [],
            ))
        return out

    return _cached(f"softgarden:{slug.lower()}", 3600, fetch)


BOARD_FETCHERS: Dict[str, Callable[..., List[JobLead]]] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "personio": personio,
    "teamtailor": teamtailor,
    "recruitee": recruitee,
    "workable": workable,
    "pinpoint": pinpoint,
    "softgarden": softgarden,
}


def _slug_candidates(company: str) -> List[str]:
    base = re.sub(r"[^a-z0-9 ]", "", company.lower()).strip()
    words = base.split()
    cands = ["".join(words), "-".join(words), words[0] if words else ""]
    camel = "".join(w.capitalize() for w in re.sub(r"[^A-Za-z0-9 ]", "", company).split())
    return [c for c in dict.fromkeys(cands + [camel]) if c]


def _probe(ats: str, slug: str, company: str) -> Optional[Tuple[str, str, int]]:
    try:
        jobs = BOARD_FETCHERS[ats](slug, company)
    except SourceError:
        return None
    return (ats, slug, len(jobs)) if jobs else None


def find_boards(company: str) -> List[Tuple[str, str, int]]:
    """Every public ATS board answering to a company's likely slugs, probed in
    parallel: [(ats, slug, open_jobs)], most jobs first.

    A slug is not an identity: "iqm" on Lever is an unrelated New York
    company, while IQM (quantum) is on Teamtailor. So callers must let the
    user confirm a hit (show sample titles); ``detect_board_from_page`` on the
    company's own careers page is the reliable route.
    """
    probes = [(ats, slug) for slug in _slug_candidates(company) for ats in BOARD_FETCHERS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        hits = [h for h in pool.map(lambda p: _probe(p[0], p[1], company), probes) if h]
    return sorted(dict.fromkeys(hits), key=lambda h: -h[2])


# Where each ATS shows up in a careers page's HTML (links, iframes, scripts).
_BOARD_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9._-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)")),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com")),
    ("workable", re.compile(r"apply\.workable\.com/(?!api\b|j/)([A-Za-z0-9_-]+)")),
    ("pinpoint", re.compile(r"([a-z0-9-]+)\.pinpointhq\.com")),
    ("softgarden", re.compile(r"([a-z0-9-]+)\.career\.softgarden\.de")),
    ("teamtailor", re.compile(r"([a-z0-9-]+)\.teamtailor\.com")),
]


def detect_board_from_page(url: str, company: str = "") -> Optional[Tuple[str, str, int]]:
    """Detect the ATS a careers page embeds, then confirm the board answers.
    Teamtailor career sites on their own domain are recognised by the
    platform's markers and probed at that host."""
    from tools.url_ingest import fetch_html

    try:
        html = fetch_html(url)
    except ValueError as exc:
        raise SourceError(str(exc)) from exc
    candidates: List[Tuple[str, str]] = []
    for ats, pattern in _BOARD_PATTERNS:
        for slug in dict.fromkeys(pattern.findall(html)):
            if slug.lower() not in ("www", "api", "embed", "assets", "static", "cdn", "app", "careers"):
                candidates.append((ats, slug))
    if "teamtailor" in html.lower():
        host = (urlparse(url).hostname or "").lower()
        if host:
            candidates.append(("teamtailor", host))
    for ats, slug in dict.fromkeys(candidates):
        hit = _probe(ats, slug, company or slug)
        if hit:
            return hit
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


# GermanTechJobs / SwissDevJobs / DevITjobs.uk: one RSS feed per country,
# every item titled "Role @ Company [salary]".
DEVJOBS_FEEDS: Dict[str, Tuple[str, str]] = {
    "de": ("https://germantechjobs.de/rss", "Germany"),
    "ch": ("https://swissdevjobs.ch/rss", "Switzerland"),
    "gb": ("https://devitjobs.uk/rss", "United Kingdom"),
}
_DEVJOBS_TITLE = re.compile(r"^(?P<title>.*?)\s*-?\s*@\s*(?P<company>.*?)\s*(?:\[(?P<salary>[^\]]*)\])?\s*$")


# A bare "remote" in a description is often "support users remote and on
# site"; only an explicit statement makes the job itself remote.
_FULLY_REMOTE = re.compile(
    r"(?:100\s*%|fully|full|komplett|vollständig)[\s-]*remote|remote[\s-]*(?:first|only|job|position|role)"
    r"|work from anywhere",
    re.IGNORECASE,
)


def _devjobs_remote(title: str, body: str) -> Optional[bool]:
    return True if re.search(r"\bremote\b", title, re.IGNORECASE) or _FULLY_REMOTE.search(body) else None


def devjobs(country: str) -> List[JobLead]:
    country = country.lower()
    if country not in DEVJOBS_FEEDS:
        raise SourceError(f"No DevJobs feed for {country!r}.")
    url, country_name = DEVJOBS_FEEDS[country]

    def fetch() -> List[JobLead]:
        channel = _get_xml(url).find("channel")
        out = []
        for it in channel.findall("item") if channel is not None else []:
            raw_title = _xml_text(it, "title")
            m = _DEVJOBS_TITLE.match(raw_title)
            body = html_to_text(_xml_text(it, "{http://purl.org/rss/1.0/modules/content/}encoded")
                                or _xml_text(it, "description"))
            out.append(JobLead(
                source="devjobs", external_id=_xml_text(it, "guid") or _xml_text(it, "link"),
                url=_xml_text(it, "link"),
                title=(m.group("title") if m else raw_title).strip(),
                company=(m.group("company") if m else "").strip(),
                location=country_name, remote=_devjobs_remote(raw_title, body),
                posted_at=_rss_date(_xml_text(it, "pubDate")),
                salary=(m.group("salary") or "").strip() if m else "",
                description=body,
            ))
        return out

    return _cached(f"devjobs:{country}", 3600, fetch)


# Aggregator sources the feed can enable, with the attribution their terms ask for.
AGGREGATORS: Dict[str, dict] = {
    "arbeitnow": {"label": "Arbeitnow (EU)", "credit": "arbeitnow.com"},
    "remotive": {"label": "Remotive (remote)", "credit": "remotive.com"},
    "remoteok": {"label": "RemoteOK (remote)", "credit": "remoteok.com"},
    "hn": {"label": "HN Who is hiring", "credit": "news.ycombinator.com"},
    "adzuna": {"label": "Adzuna (UK/EU, needs key)", "credit": "adzuna.com"},
    "devjobs": {"label": "GermanTechJobs / SwissDevJobs / DevITjobs (salary)",
                "credit": "germantechjobs.de, swissdevjobs.ch, devitjobs.uk"},
}
