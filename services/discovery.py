"""Job discovery: gather → prefilter → dedupe/upsert → rank → triage.

1. **Gather** leads from the user's company watchlist (Greenhouse / Lever /
   Ashby / SmartRecruiters boards) and the enabled aggregators (Arbeitnow,
   Remotive, RemoteOK, HN "Who is hiring", Adzuna), in parallel. A failing
   source is reported, never fatal.
2. **Prefilter** against the profile: the title must match a target role;
   aggregator posts older than ``max_posting_age_days`` are dropped (watchlist
   boards only list open roles, so their age doesn't matter).
3. **Upsert** per user, deduplicated by URL (or company + title), so a job seen
   again keeps its status (dismissed stays dismissed).
4. **Rank** with a deterministic ``match_score`` (title, skills in the posting,
   location / remote fit, salary vs. floor) and flags.
5. **Triage** the best-ranked unscored jobs with the cheap model: a 0-100 fit
   score for *this* candidate, a one-line reason, and concerns.

Deep analysis of a feed item reuses the normal pipeline (``posting_text_for``
gives it the full text, fetching the page when a source only has a snippet).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import func, select

from db.models import DISCOVERED_JOB_STATUSES, DiscoveredJob
from db.session import get_session
from services._ownership import require_owned
from services.profile import CandidateProfile, get_profile, profile_as_prompt
from tools import job_sources
from tools.job_sources import JobLead
from utils.llm import get_completion, web_research, web_research_available
from utils.security import wrap_untrusted
from utils.text import strip_code_fence

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = ("watchlist", "arbeitnow", "remotive", "remoteok", "hn")
TRIAGE_BATCH = 8
_DESCRIPTION_CAP = 20_000


class DiscoveryError(ValueError):
    """User-facing failure (unknown job, bad status, missing profile...)."""


@dataclass
class JobRecord:
    id: int
    source: str
    url: str
    title: str
    company: str
    location: str
    remote: Optional[bool]
    posted_at: Optional[datetime]
    salary_text: str
    description: str
    match_score: Optional[int]
    fit_score: Optional[int]
    fit_summary: str
    fit_flags: List[str]
    status: str
    application_id: Optional[int]
    first_seen_at: datetime


def _to_record(r: DiscoveredJob) -> JobRecord:
    return JobRecord(
        id=r.id, source=r.source, url=r.url or "", title=r.title, company=r.company or "",
        location=r.location or "", remote=r.remote, posted_at=r.posted_at,
        salary_text=r.salary_text or "", description=r.description or "",
        match_score=r.match_score, fit_score=r.fit_score, fit_summary=r.fit_summary or "",
        fit_flags=list(r.fit_flags or []), status=r.status, application_id=r.application_id,
        first_seen_at=r.first_seen_at,
    )


@dataclass
class RefreshReport:
    fetched: int = 0
    kept: int = 0
    new: int = 0
    seen_again: int = 0
    triaged: int = 0
    per_source: Dict[str, int] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------

_SENIORITY = {
    "senior", "sr", "junior", "jr", "lead", "staff", "principal", "head", "chief", "mid",
    "intern", "associate", "i", "ii", "iii", "iv", "m", "w", "d", "f", "x",
}
_STOP = {"of", "and", "the", "for", "a", "an", "to", "in", "at", "with", "or", "en", "de"}
_PHRASES = [
    (r"back[\s-]+end", "backend"), (r"front[\s-]+end", "frontend"), (r"full[\s-]+stack", "fullstack"),
    (r"\bml\b", "machine learning"), (r"\bai\b", "artificial intelligence"),
    (r"\bswe\b", "software engineer"), (r"\bsre\b", "site reliability engineer"),
    (r"\bdeveloper\b|\bprogrammer\b|\bdev\b", "engineer"), (r"\bengineers\b", "engineer"),
    (r"\bdata scientist\b", "data scientist"),
]


def _title_terms(title: str) -> Set[str]:
    t = (title or "").lower()
    for pattern, repl in _PHRASES:
        t = re.sub(pattern, repl, t)
    words = re.findall(r"[a-z0-9+#.]+", t)
    return {w.strip(".") for w in words if w.strip(".") and w not in _SENIORITY and w not in _STOP}


def title_match(job_title: str, targets: Iterable[str]) -> float:
    """Best share of a target title's core terms present in the job title."""
    job = _title_terms(job_title)
    best = 0.0
    for target in targets:
        terms = _title_terms(target)
        if terms:
            best = max(best, len(terms & job) / len(terms))
    return best


# ---------------------------------------------------------------------------
# Deterministic ranking
# ---------------------------------------------------------------------------

_EU_MARKERS = re.compile(
    r"europe|\beu\b|emea|\buk\b|united kingdom|england|ireland|germany|deutschland|netherlands|"
    r"france|spain|portugal|italy|poland|austria|switzerland|belgium|sweden|denmark|norway|"
    r"finland|czech|estonia|berlin|munich|hamburg|amsterdam|london|paris|madrid|barcelona|"
    r"lisbon|warsaw|vienna|zurich|dublin|stockholm|copenhagen|prague|\bcet\b|\bcest\b|gmt|utc",
    re.IGNORECASE,
)
_US_ONLY = re.compile(
    r"\b(us|usa|u\.s\.|united states|north america|americas|canada|latam|us time ?zones?)\b",
    re.IGNORECASE,
)
_WORLDWIDE = re.compile(r"worldwide|anywhere|global", re.IGNORECASE)
_MONEY_RE = re.compile(r"(\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s*([kK])?")
_CURRENCIES = {"€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "$": "USD", "usd": "USD", "chf": "CHF"}


def _user_in_europe(profile: CandidateProfile) -> bool:
    text = " ".join(profile.locations + [profile.remote_regions, profile.work_authorization])
    return bool(_EU_MARKERS.search(text))


def _is_remote(lead: JobLead) -> bool:
    return bool(lead.remote) or "remote" in (lead.location or "").lower()


def _location_fit(lead: JobLead, profile: CandidateProfile) -> Tuple[float, List[str]]:
    flags: List[str] = []
    loc = lead.location or ""
    modes = set(profile.work_modes)
    if _is_remote(lead):
        if modes and "remote" not in modes:
            return 0.5, flags
        if _user_in_europe(profile) and _US_ONLY.search(loc) and not (
            _EU_MARKERS.search(loc) or _WORLDWIDE.search(loc)
        ):
            flags.append("Remote, but restricted to the Americas")
            return 0.2, flags
        return 1.0, flags
    if modes == {"remote"}:
        flags.append("Not remote")
        return 0.1, flags
    if not loc:
        return 0.6, flags
    wanted = [w.lower() for w in profile.locations if w]
    if not wanted:
        return 0.6, flags
    if any(w in loc.lower() for w in wanted):
        return 1.0, flags
    flags.append(f"Location: {loc[:60]}")
    return 0.2, flags


def parse_salary_max(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Highest annual-looking figure and its currency from a salary string."""
    if not text or re.search(r"per (hour|day|task)|/h\b|hourly|daily", text, re.IGNORECASE):
        return None, None
    currency = next((code for sym, code in _CURRENCIES.items() if sym in text.lower()), None)
    values = []
    for num, k in _MONEY_RE.findall(text):
        v = float(re.sub(r"[,.\s](?=\d{3}\b)", "", num).replace(",", ""))
        if k:
            v *= 1000
        if re.search(r"per month|/month|monthly|/mo\b", text, re.IGNORECASE):
            v *= 12
        if v >= 10_000:
            values.append(v)
    return (max(values) if values else None), currency


def _salary_fit(lead: JobLead, profile: CandidateProfile) -> Tuple[float, List[str]]:
    if not profile.salary_floor:
        return 0.5, []
    top, currency = parse_salary_max(lead.salary)
    if top is None or (currency and currency != profile.salary_currency):
        return 0.5, []
    if top < profile.salary_floor:
        return 0.0, [f"Salary below your floor ({lead.salary})"]
    return 1.0, []


def _skill_fit(lead: JobLead, skills: List[str]) -> float:
    if not skills:
        return 0.5
    text = f"{lead.title}\n{' '.join(lead.tags)}\n{lead.description}".lower()
    hits = sum(
        1 for s in skills
        if re.search(r"(?<![a-z0-9+#])" + re.escape(s.lower()) + r"(?![a-z0-9+#])", text)
    )
    return min(1.0, hits / max(1, min(len(skills), 6)))


_LEVELS = [
    (r"\bintern|werkstudent|working student", 0), (r"\bjunior\b|\bjr\b|graduate|entry", 1),
    (r"\bmid\b|intermediate", 2), (r"\bsenior\b|\bsr\b", 3),
    (r"\bstaff\b|\blead\b", 4), (r"\bprincipal\b|\bdistinguished\b", 5),
    (r"\bhead of\b|\bdirector\b|\bvp\b|\bchief\b", 6),
]


def _level(text: str) -> Optional[int]:
    """Seniority rank named in a title; the highest one wins ("Senior/Staff" -> staff)."""
    found = [rank for pattern, rank in _LEVELS if re.search(pattern, text or "", re.IGNORECASE)]
    return max(found) if found else None


def score_lead(lead: JobLead, profile: CandidateProfile) -> Tuple[int, List[str]]:
    """Deterministic 0-100 pre-rank plus human-readable flags."""
    targets = profile.target_titles or profile.search_keywords
    title = title_match(lead.title, targets) if targets else 0.5
    location, loc_flags = _location_fit(lead, profile)
    salary, sal_flags = _salary_fit(lead, profile)
    skills = _skill_fit(lead, profile.search_keywords + profile.must_haves)
    score = 100 * (0.45 * title + 0.25 * skills + 0.20 * location + 0.10 * salary)
    flags = loc_flags + sal_flags
    level, wanted = _level(lead.title), _level(profile.seniority)
    if level is not None and wanted is not None and abs(level - wanted) >= 2:
        flags.append(f"Seniority: {lead.title.split(',')[0][:50]} (you: {profile.seniority})")
        score -= 10
    haystack = f"{lead.title} {lead.company} {' '.join(lead.tags)}".lower()
    for term in profile.dealbreakers + profile.industries_avoid:
        if len(term) <= 40 and term.lower() in haystack:
            flags.append(f"Possible dealbreaker: {term}")
            score -= 25
    return max(0, min(100, round(score))), flags


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------

_COUNTRY_CODES = [
    (r"\buk\b|united kingdom|england|scotland|london|manchester|edinburgh", "gb"),
    (r"germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|cologne|köln", "de"),
    (r"netherlands|amsterdam|rotterdam|utrecht|eindhoven", "nl"),
    (r"france|paris|lyon", "fr"), (r"austria|vienna|wien", "at"),
    (r"belgium|brussels|antwerp", "be"), (r"switzerland|zurich|zürich|geneva|basel", "ch"),
    (r"spain|madrid|barcelona|valencia", "es"), (r"italy|milan|rome|turin", "it"),
    (r"poland|warsaw|krakow|kraków|wroclaw", "pl"),
]


def _adzuna_countries(profile: CandidateProfile) -> List[str]:
    text = " ".join(profile.locations).lower()
    codes = [code for pattern, code in _COUNTRY_CODES if re.search(pattern, text)]
    return list(dict.fromkeys(codes))[:3] or ["gb", "de"]


def _search_terms(profile: CandidateProfile) -> List[str]:
    """Target titles without seniority words, as typed ("Senior ML Engineer" -> "ML Engineer")."""
    terms = [
        " ".join(w for w in re.findall(r"[A-Za-z0-9+#.]+", t) if w.lower() not in _SENIORITY | _STOP)
        for t in profile.target_titles
    ]
    terms = [t for t in terms if t] or profile.search_keywords[:2]
    return list(dict.fromkeys(terms))[:3]


def _source_tasks(profile: CandidateProfile, sources: Iterable[str]) -> List[Tuple[str, Callable[[], List[JobLead]]]]:
    tasks: List[Tuple[str, Callable[[], List[JobLead]]]] = []
    wanted = set(sources)
    if "watchlist" in wanted:
        for w in profile.watchlist:
            fetcher = job_sources.BOARD_FETCHERS[w["ats"]]
            tasks.append((f"{w['ats']}:{w['company']}", partial(fetcher, w["slug"], w["company"])))
    if "arbeitnow" in wanted:
        tasks.append(("arbeitnow", partial(job_sources.arbeitnow, pages=2)))
    if "remotive" in wanted:
        for term in _search_terms(profile):
            tasks.append((f"remotive:{term}", partial(job_sources.remotive, term)))
    if "remoteok" in wanted:
        tasks.append(("remoteok", job_sources.remoteok))
    if "hn" in wanted:
        tasks.append(("hn", job_sources.hn_whos_hiring))
    if "adzuna" in wanted and job_sources.adzuna_available():
        for country in _adzuna_countries(profile):
            for term in _search_terms(profile)[:2]:
                tasks.append((f"adzuna:{country}:{term}", partial(job_sources.adzuna, country, term)))
    return tasks


def gather(profile: CandidateProfile, sources: Iterable[str]) -> Tuple[List[Tuple[str, JobLead]], RefreshReport]:
    report = RefreshReport()
    results: List[Tuple[str, JobLead]] = []
    tasks = _source_tasks(profile, sources)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {name: pool.submit(fn) for name, fn in tasks}
        for name, fut in futures.items():
            try:
                leads = fut.result()
            except Exception as exc:  # noqa: BLE001 - a failing source isn't fatal
                report.errors[name] = str(exc)[:300]
                continue
            report.per_source[name] = len(leads)
            results.extend((name, lead) for lead in leads)
    report.fetched = len(results)
    return results, report


def prefilter(task: str, lead: JobLead, profile: CandidateProfile, now: Optional[datetime] = None) -> bool:
    if not lead.title or not lead.url:
        return False
    targets = profile.target_titles or profile.search_keywords
    if targets and title_match(lead.title, targets) < 0.66:
        return False
    from_watchlist = task.split(":", 1)[0] in job_sources.BOARD_FETCHERS
    if not from_watchlist and lead.posted_at:
        age = (now or datetime.utcnow()) - lead.posted_at
        if age > timedelta(days=profile.max_posting_age_days):
            return False
    return True


def dedupe_key(lead: JobLead) -> str:
    url = re.sub(r"[?#].*$", "", (lead.url or "").strip().lower()).rstrip("/")
    if url:
        return url[:255]
    return f"{lead.company}|{lead.title}|{lead.location}".lower()[:255]


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def refresh_feed(
    user_id: int,
    sources: Optional[Iterable[str]] = None,
    triage_limit: int = 40,
    progress: Optional[Callable[[str], None]] = None,
) -> RefreshReport:
    profile = get_profile(user_id)
    if profile.is_empty() and not profile.watchlist:
        raise DiscoveryError("Set target roles (or keywords) in your profile first.")
    say = progress or (lambda _msg: None)
    say("Fetching job sources…")
    leads, report = gather(profile, sources or DEFAULT_SOURCES)
    kept = [(t, lead) for t, lead in leads if prefilter(t, lead, profile)]
    report.kept = len(kept)
    say(f"Kept {len(kept)} of {report.fetched} postings matching your profile; ranking…")
    _upsert(user_id, [lead for _t, lead in kept], profile, report)
    if triage_limit:
        say("Scoring fit with the model…")
        report.triaged = triage(user_id, limit=triage_limit)
    return report


def _upsert(user_id: int, leads: List[JobLead], profile: CandidateProfile, report: RefreshReport) -> None:
    now = datetime.utcnow()
    by_key: Dict[str, JobLead] = {}
    for lead in leads:
        by_key.setdefault(dedupe_key(lead), lead)
    with get_session() as session:
        existing = {
            r.dedupe_key: r for r in session.execute(
                select(DiscoveredJob).where(
                    DiscoveredJob.user_id == user_id, DiscoveredJob.dedupe_key.in_(list(by_key))
                )
            ).scalars()
        } if by_key else {}
        for key, lead in by_key.items():
            score, flags = score_lead(lead, profile)
            row = existing.get(key)
            if row is None:
                session.add(DiscoveredJob(
                    user_id=user_id, source=lead.source, external_id=lead.external_id[:255],
                    dedupe_key=key, url=lead.url, title=lead.title[:255],
                    company=(lead.company or "")[:255], location=(lead.location or "")[:255],
                    remote=lead.remote, posted_at=lead.posted_at,
                    salary_text=(lead.salary or "")[:255] or None,
                    description=(lead.description or "")[:_DESCRIPTION_CAP],
                    match_score=score, fit_flags=flags, status="new",
                    first_seen_at=now, last_seen_at=now,
                ))
                report.new += 1
            else:
                row.last_seen_at = now
                row.match_score = score
                if lead.description and len(lead.description) > len(row.description or ""):
                    row.description = lead.description[:_DESCRIPTION_CAP]
                report.seen_again += 1
        session.commit()


def rescore(user_id: int) -> int:
    """Recompute deterministic scores after a profile change."""
    profile = get_profile(user_id)
    with get_session() as session:
        rows = session.execute(select(DiscoveredJob).where(DiscoveredJob.user_id == user_id)).scalars().all()
        for r in rows:
            lead = JobLead(
                source=r.source, external_id=r.external_id or "", url=r.url or "", title=r.title,
                company=r.company or "", location=r.location or "", remote=r.remote,
                posted_at=r.posted_at, salary=r.salary_text or "", description=r.description or "",
            )
            r.match_score, flags = score_lead(lead, profile)
            r.fit_flags = flags if r.fit_score is None else r.fit_flags
        session.commit()
        return len(rows)


# ---------------------------------------------------------------------------
# LLM triage
# ---------------------------------------------------------------------------

_TRIAGE_PROMPT = """\
You are screening job postings for ONE specific candidate. Judge fit for them,
not general attractiveness.

CANDIDATE PROFILE (what they want):
{profile}

CANDIDATE CV (excerpt; what they have):
{cv}

POSTINGS:
{postings}

For each posting, score fit 0-100 for this candidate: role and seniority match,
evidence of the required skills in the CV, location / remote region / work
authorization, salary vs. their floor, must-haves and dealbreakers. Be strict:
80+ only if you'd tell them to apply today; under 40 if a dealbreaker or hard
requirement clearly fails.

Return STRICT JSON only, an array with one object per posting:
[{{"id": <posting id>, "fit": <0-100>, "summary": "<one concrete sentence>",
   "flags": ["<short concern>", ...]}}]
"""


def _cv_excerpt(user_id: int, limit: int = 4000) -> str:
    from services.master_cv import get_master_cv

    cv = get_master_cv(user_id)
    return cv.raw_text[:limit] if cv else "(no master CV saved)"


def triage(user_id: int, limit: int = 40, job_ids: Optional[List[int]] = None) -> int:
    """Score the best-ranked untriaged jobs with the fast model. Returns how
    many were scored. Skipped silently in demo mode (no key)."""
    from services.analysis_runner import metered_llm
    from utils.llm import is_demo_mode

    if is_demo_mode():
        return 0
    profile = get_profile(user_id)
    with get_session() as session:
        q = select(DiscoveredJob).where(
            DiscoveredJob.user_id == user_id, DiscoveredJob.status == "new",
        )
        if job_ids:
            q = q.where(DiscoveredJob.id.in_(job_ids))
        else:
            q = q.where(DiscoveredJob.fit_score.is_(None))
        rows = session.execute(
            q.order_by(DiscoveredJob.match_score.desc().nullslast()).limit(limit)
        ).scalars().all()
        jobs = [_to_record(r) for r in rows]
    if not jobs:
        return 0

    profile_text = profile_as_prompt(profile) or "(no profile set)"
    cv = wrap_untrusted(_cv_excerpt(user_id), "master_cv")
    scored = 0
    for i in range(0, len(jobs), TRIAGE_BATCH):
        batch = jobs[i:i + TRIAGE_BATCH]
        postings = "\n\n".join(
            f"[id {j.id}] {j.title} | {j.company} | {j.location or 'location n/a'}"
            f"{' | remote' if j.remote else ''}{' | ' + j.salary_text if j.salary_text else ''}"
            f"\n{j.description[:1200]}"
            for j in batch
        )
        prompt = _TRIAGE_PROMPT.format(
            profile=profile_text, cv=cv, postings=wrap_untrusted(postings, "job_postings")
        )
        try:
            with metered_llm(user_id, "fast") as tier:
                raw = get_completion(prompt, tier)
            items = json.loads(strip_code_fence(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Triage batch skipped (unparsable output): %s", exc)
            continue
        scored += _apply_triage(user_id, items, {j.id for j in batch})
    return scored


def _apply_triage(user_id: int, items, allowed_ids: Set[int]) -> int:
    n = 0
    with get_session() as session:
        for item in items if isinstance(items, list) else []:
            try:
                job_id = int(item.get("id"))
                fit = max(0, min(100, int(item.get("fit"))))
            except (TypeError, ValueError, AttributeError):
                continue
            if job_id not in allowed_ids:
                continue
            row = session.get(DiscoveredJob, job_id)
            if row is None or row.user_id != user_id:
                continue
            row.fit_score = fit
            row.fit_summary = str(item.get("summary") or "")[:500]
            llm_flags = [str(f)[:120] for f in (item.get("flags") or [])][:6]
            row.fit_flags = list(dict.fromkeys((row.fit_flags or []) + llm_flags))
            n += 1
        session.commit()
    return n


# ---------------------------------------------------------------------------
# Open-web discovery (Claude web search)
# ---------------------------------------------------------------------------

_WEB_DISCOVERY_PROMPT = """\
Find currently open job postings that fit this candidate. Use web search;
prefer the employer's own careers page or ATS (Greenhouse, Lever, Ashby,
Workday, Personio, SmartRecruiters, Recruitee, Teamtailor) over aggregators,
and skip postings that are closed or older than {max_age} days.

CANDIDATE PROFILE:
{profile}

Return STRICT JSON only (no prose): an array of up to 15 objects
[{{"title": "...", "company": "...", "url": "<direct posting URL>",
   "location": "...", "remote": true|false|null, "salary": "<if stated>",
   "why": "<one sentence on why it fits>"}}]
"""


def web_discover(user_id: int, max_searches: int = 8) -> RefreshReport:
    """Ask Claude (with live web search) for postings beyond the API sources."""
    if not web_research_available():
        raise DiscoveryError("Web discovery needs ANTHROPIC_API_KEY.")
    from services.analysis_runner import metered_llm

    profile = get_profile(user_id)
    if profile.is_empty():
        raise DiscoveryError("Set target roles in your profile first.")
    prompt = _WEB_DISCOVERY_PROMPT.format(
        profile=profile_as_prompt(profile), max_age=profile.max_posting_age_days
    )
    with metered_llm(user_id, "detailed"):
        text, _sources = web_research(prompt, max_searches=max_searches, max_fetches=3)
    match = re.search(r"\[.*\]", strip_code_fence(text), re.DOTALL)
    try:
        items = json.loads(match.group(0)) if match else []
    except json.JSONDecodeError as exc:
        raise DiscoveryError(f"Couldn't read the web search results: {exc}") from exc
    leads = [
        JobLead(
            source="web", external_id=str(it.get("url", ""))[:255], url=str(it.get("url", "")),
            title=str(it.get("title", ""))[:255], company=str(it.get("company", "")),
            location=str(it.get("location", "")), remote=it.get("remote"),
            salary=str(it.get("salary") or ""), description=str(it.get("why") or ""),
        )
        for it in items if isinstance(it, dict) and it.get("url") and it.get("title")
    ]
    report = RefreshReport(fetched=len(leads), per_source={"web": len(leads)})
    leads = [lead for lead in leads if lead.url.startswith(("http://", "https://"))]
    report.kept = len(leads)
    _upsert(user_id, leads, profile, report)
    return report


# ---------------------------------------------------------------------------
# Feed queries and actions
# ---------------------------------------------------------------------------

def list_feed(user_id: int, status: str = "new", limit: int = 200) -> List[JobRecord]:
    if status not in DISCOVERED_JOB_STATUSES:
        raise DiscoveryError(f"Unknown status {status!r}.")
    with get_session() as session:
        rows = session.execute(
            select(DiscoveredJob)
            .where(DiscoveredJob.user_id == user_id, DiscoveredJob.status == status)
            .order_by(
                DiscoveredJob.fit_score.desc().nullslast(),
                DiscoveredJob.match_score.desc().nullslast(),
                DiscoveredJob.posted_at.desc().nullslast(),
            )
            .limit(limit)
        ).scalars().all()
        return [_to_record(r) for r in rows]


def feed_counts(user_id: int) -> Dict[str, int]:
    with get_session() as session:
        rows = session.execute(
            select(DiscoveredJob.status, func.count())
            .where(DiscoveredJob.user_id == user_id)
            .group_by(DiscoveredJob.status)
        ).all()
    counts = {s: 0 for s in DISCOVERED_JOB_STATUSES}
    counts.update({status: n for status, n in rows})
    return counts


def get_job(user_id: int, job_id: int) -> JobRecord:
    with get_session() as session:
        return _to_record(require_owned(session, DiscoveredJob, job_id, user_id, DiscoveryError, "Job not found."))


def set_status(user_id: int, job_id: int, status: str, application_id: Optional[int] = None) -> JobRecord:
    if status not in DISCOVERED_JOB_STATUSES:
        raise DiscoveryError(f"Unknown status {status!r}.")
    with get_session() as session:
        row = require_owned(session, DiscoveredJob, job_id, user_id, DiscoveryError, "Job not found.")
        row.status = status
        if application_id is not None:
            row.application_id = application_id
        session.commit()
        return _to_record(row)


def posting_text_for(user_id: int, job_id: int) -> str:
    """Full posting text for deep analysis. Fetches the page when the source
    only had a snippet (SmartRecruiters, Adzuna, web finds) and stores it."""
    job = get_job(user_id, job_id)
    body = job.description
    if len(body) < 400 and job.url:
        from tools.url_ingest import fetch_job_posting

        try:
            body = fetch_job_posting(job.url)
            with get_session() as session:
                row = session.get(DiscoveredJob, job_id)
                if row is not None and row.user_id == user_id:
                    row.description = body[:_DESCRIPTION_CAP]
                    session.commit()
        except ValueError as exc:
            logger.info("Couldn't fetch %s for analysis: %s", job.url, exc)
    header = [
        f"Company: {job.company}" if job.company else "",
        f"Title: {job.title}",
        f"Location: {job.location}" if job.location else "",
        f"Compensation: {job.salary_text}" if job.salary_text else "",
        f"Source: {job.url}" if job.url else "",
    ]
    return "\n".join(h for h in header if h) + "\n\n" + body


def track(user_id: int, job_id: int, analysis_result: Optional[dict] = None) -> int:
    """Save a feed item to the application tracker; returns the application id."""
    from services.applications import save_analysis

    job = get_job(user_id, job_id)
    result = analysis_result or {
        "job_posting": posting_text_for(user_id, job_id),
        "job_details": {"extracted_details": {
            "company_name": job.company, "job_title": job.title, "location": job.location,
            "compensation": job.salary_text,
        }},
    }
    result = dict(result, discovered_job={"source": job.source, "url": job.url})
    app = save_analysis(
        user_id,
        {"company_name": job.company or "Unknown company", "job_title": job.title,
         "location": job.location, "compensation": job.salary_text},
        result,
        status="saved",
        notes=f"Found via {job.source}: {job.url}" if job.url else None,
    )
    set_status(user_id, job_id, "saved", application_id=app.id)
    return app.id
