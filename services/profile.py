"""The candidate's search profile: what they're looking for.

Personalisation hub. The master CV says what the candidate *has*; the profile
says what they *want* (target roles, seniority, locations, work mode, salary
floor, must-haves, dealbreakers, industries, a company watchlist). It feeds:

  * the analysis report: a "Fit for you" section, with dealbreakers and the
    salary floor weighed in the verdict;
  * job discovery: search queries, prefiltering, and fit triage;
  * the deterministic match score (``services.discovery``).

Stored as one JSON document per user (``candidate_profiles.data``) so fields
can be added without a migration. Unknown keys in stored data are ignored
and missing ones take their defaults.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from db.models import CandidateProfile as ProfileRow
from db.session import get_session
from tools.job_sources import BOARD_FETCHERS
from utils.llm import get_completion
from utils.security import wrap_untrusted
from utils.text import strip_code_fence

logger = logging.getLogger(__name__)

WORK_MODES = ("remote", "hybrid", "onsite")
COMPANY_STAGES = ("startup", "scale-up", "enterprise", "public sector", "non-profit")
# Supported company-board providers for the watchlist.
WATCHLIST_ATS = tuple(BOARD_FETCHERS)


class ProfileError(ValueError):
    """User-facing failure (bad watchlist entry, unparsable CV suggestion...)."""


@dataclass
class CandidateProfile:
    target_titles: List[str] = field(default_factory=list)
    seniority: str = ""
    locations: List[str] = field(default_factory=list)
    work_modes: List[str] = field(default_factory=list)
    remote_regions: str = ""
    work_authorization: str = ""
    salary_floor: Optional[int] = None
    salary_currency: str = "EUR"
    must_haves: List[str] = field(default_factory=list)
    dealbreakers: List[str] = field(default_factory=list)
    industries_prefer: List[str] = field(default_factory=list)
    industries_avoid: List[str] = field(default_factory=list)
    company_stages: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    search_keywords: List[str] = field(default_factory=list)
    # [{"company": "Spotify", "ats": "lever", "slug": "spotify"}, ...]
    watchlist: List[dict] = field(default_factory=list)
    max_posting_age_days: int = 30
    notes: str = ""

    def is_empty(self) -> bool:
        return not (self.target_titles or self.search_keywords or self.notes)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CandidateProfile":
        data = data or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def _clean_list(values) -> List[str]:
    if isinstance(values, str):  # e.g. stored by an older client
        values = values.splitlines()
    out: List[str] = []
    for v in values or []:
        s = str(v).strip()
        if s and s.lower() not in {o.lower() for o in out}:
            out.append(s)
    return out


def normalize(profile: CandidateProfile) -> CandidateProfile:
    """Trim/dedupe list fields, clamp enums, validate the watchlist."""
    p = CandidateProfile.from_dict(asdict(profile))
    for name in (
        "target_titles", "locations", "must_haves", "dealbreakers", "industries_prefer",
        "industries_avoid", "languages", "search_keywords",
    ):
        setattr(p, name, _clean_list(getattr(p, name)))
    p.work_modes = [m for m in _clean_list(p.work_modes) if m in WORK_MODES]
    p.company_stages = [s for s in _clean_list(p.company_stages) if s in COMPANY_STAGES]
    p.salary_floor = int(p.salary_floor) if p.salary_floor else None
    p.salary_currency = (p.salary_currency or "EUR").strip().upper()[:3]
    p.max_posting_age_days = max(1, min(int(p.max_posting_age_days or 30), 365))
    watch: List[dict] = []
    for entry in p.watchlist or []:
        ats = str(entry.get("ats", "")).strip().lower()
        slug = str(entry.get("slug", "")).strip()
        if ats not in WATCHLIST_ATS or not slug:
            raise ProfileError(f"Watchlist entry needs a known ATS and board slug: {entry!r}")
        key = (ats, slug.lower())
        if key not in {(w["ats"], w["slug"].lower()) for w in watch}:
            watch.append({"company": str(entry.get("company") or slug).strip(), "ats": ats, "slug": slug})
    p.watchlist = watch
    p.notes = (p.notes or "").strip()
    return p


def profile_prompt_for(user_id: int) -> str:
    """The user's profile as prompt text, best-effort: personalisation must
    never break an analysis (e.g. on a database not yet migrated to the
    candidate_profiles table), so a DB error yields "" and a log line."""
    try:
        return profile_as_prompt(get_profile(user_id))
    except SQLAlchemyError as exc:
        logger.warning("Profile unavailable for user %s (run `alembic upgrade head`?): %s", user_id, exc)
        return ""


def get_profile(user_id: int) -> CandidateProfile:
    with get_session() as session:
        row = session.execute(
            select(ProfileRow).where(ProfileRow.user_id == user_id)
        ).scalar_one_or_none()
        return CandidateProfile.from_dict(row.data if row else None)


def save_profile(user_id: int, profile: CandidateProfile) -> CandidateProfile:
    profile = normalize(profile)
    with get_session() as session:
        row = session.execute(
            select(ProfileRow).where(ProfileRow.user_id == user_id)
        ).scalar_one_or_none()
        if row is None:
            row = ProfileRow(user_id=user_id, data={})
            session.add(row)
        row.data = asdict(profile)
        row.updated_at = datetime.utcnow()
        session.commit()
    return profile


def profile_as_prompt(profile: CandidateProfile) -> str:
    """Render the profile for an LLM prompt; empty string when unset."""
    if profile.is_empty():
        return ""
    lines = []

    def add(label: str, value) -> None:
        if isinstance(value, list):
            value = ", ".join(map(str, value))
        if value:
            lines.append(f"- {label}: {value}")

    add("Target roles", profile.target_titles)
    add("Seniority", profile.seniority)
    add("Preferred locations", profile.locations)
    add("Work mode", profile.work_modes)
    add("Remote region / time zones", profile.remote_regions)
    add("Work authorization", profile.work_authorization)
    if profile.salary_floor:
        add("Minimum acceptable base salary", f"{profile.salary_floor:,} {profile.salary_currency}/year")
    add("Must-haves", profile.must_haves)
    add("Dealbreakers", profile.dealbreakers)
    add("Industries to prefer", profile.industries_prefer)
    add("Industries to avoid", profile.industries_avoid)
    add("Company stage", profile.company_stages)
    add("Languages", profile.languages)
    add("Key skills / keywords", profile.search_keywords)
    add("Other notes from the candidate", profile.notes)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Suggest a starting profile from the master CV
# ---------------------------------------------------------------------------

_SUGGEST_PROMPT = """\
From this CV, draft the candidate's job-search profile. Infer only what the CV
supports; leave a field empty when it doesn't.

{cv}

Return STRICT JSON with exactly these keys:
{{"target_titles": ["2-4 realistic next-step job titles"],
  "seniority": "e.g. Senior / Staff / Lead",
  "locations": ["city or country the candidate is based in"],
  "languages": ["spoken languages listed in the CV"],
  "search_keywords": ["6-12 core skills/technologies that best describe them"]}}
No prose, no markdown fences.
"""


def suggest_from_cv(user_id: int, model: str = "fast") -> CandidateProfile:
    """Draft target titles / seniority / location / keywords from the master
    CV. Returns a profile *merged into* the saved one (existing non-empty
    fields win); the caller decides whether to save it."""
    from services.analysis_runner import metered_llm
    from services.master_cv import get_master_cv

    cv = get_master_cv(user_id)
    if cv is None:
        raise ProfileError("Save a master CV first (CV & Profile tab).")
    prompt = _SUGGEST_PROMPT.format(cv=wrap_untrusted(cv.raw_text, "master_cv"))
    with metered_llm(user_id, model) as tier:
        raw = get_completion(prompt, tier)
    try:
        suggested = json.loads(strip_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Couldn't read the suggestion: {exc}") from exc

    current = get_profile(user_id)
    merged = asdict(current)
    for key in ("target_titles", "seniority", "locations", "languages", "search_keywords"):
        if not merged.get(key) and suggested.get(key):
            merged[key] = suggested[key]
    return normalize(CandidateProfile.from_dict(merged))
