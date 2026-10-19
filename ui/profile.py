"""Search-profile editor (CV & Profile tab): what the candidate is looking for."""

from __future__ import annotations

from typing import List

import streamlit as st

from services.profile import (
    COMPANY_STAGES,
    WATCHLIST_ATS,
    WORK_MODES,
    CandidateProfile,
    ProfileError,
    get_profile,
    save_profile,
    suggest_from_cv,
)

_CURRENCIES = ["EUR", "GBP", "USD", "CHF", "PLN", "SEK", "DKK", "NOK", "CZK"]


def _lines(values) -> str:
    return "\n".join(values or [])


def _per_line(text: str) -> List[str]:
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


def _per_comma(text: str) -> List[str]:
    return [x.strip() for x in (text or "").replace("\n", ",").split(",") if x.strip()]


def render_profile_editor(user_id: int) -> None:
    st.markdown(
        "Your **search profile** personalises everything: the *Fit for you* section and "
        "verdict of every analysis, and what job discovery fetches and how it ranks it. "
        "Your master CV is what you *have*; this is what you *want*."
    )
    draft = st.session_state.pop("profile_draft", None)
    profile: CandidateProfile = draft or get_profile(user_id)
    if draft:
        st.info("Draft filled from your master CV. Review it, then **Save profile**.")

    if st.button("✨ Draft from my master CV", key="profile_from_cv"):
        with st.spinner("Reading your CV…"):
            try:
                st.session_state["profile_draft"] = suggest_from_cv(user_id)
                st.rerun()
            except ProfileError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001 - provider/LLM failure
                st.error(f"Couldn't draft a profile: {exc}")

    with st.form("profile_form"):
        c1, c2 = st.columns(2)
        with c1:
            titles = st.text_area(
                "Target roles (one per line)", _lines(profile.target_titles),
                placeholder="Senior Backend Engineer\nPlatform Engineer",
            )
            seniority = st.text_input("Seniority", profile.seniority, placeholder="Senior")
            locations = st.text_input(
                "Locations you'd work in (comma-separated)", ", ".join(profile.locations),
                placeholder="Berlin, Germany, Amsterdam",
            )
            work_modes = st.multiselect("Work mode", WORK_MODES, default=profile.work_modes)
            remote_regions = st.text_input(
                "Remote region / time zones", profile.remote_regions, placeholder="EU / CET ±2h",
            )
            authorization = st.text_input(
                "Work authorization", profile.work_authorization,
                placeholder="EU citizen; would need UK sponsorship",
            )
        with c2:
            s1, s2 = st.columns([2, 1])
            with s1:
                floor = st.number_input(
                    "Minimum base salary (per year)", min_value=0, step=5000,
                    value=int(profile.salary_floor or 0),
                )
            with s2:
                currency = st.selectbox(
                    "Currency", _CURRENCIES,
                    index=_CURRENCIES.index(profile.salary_currency)
                    if profile.salary_currency in _CURRENCIES else 0,
                )
            keywords = st.text_input(
                "Key skills to search for", ", ".join(profile.search_keywords),
                placeholder="Python, Go, Kubernetes, AWS, PostgreSQL",
            )
            must = st.text_area("Must-haves (one per line)", _lines(profile.must_haves),
                                placeholder="4-day week or flexible hours\nEnglish-speaking team")
            deal = st.text_area("Dealbreakers (one per line)", _lines(profile.dealbreakers),
                                placeholder="Crypto / gambling\nOn-call every week")
        c3, c4 = st.columns(2)
        with c3:
            prefer = st.text_input("Industries to prefer", ", ".join(profile.industries_prefer),
                                   placeholder="climate, health, developer tools")
            avoid = st.text_input("Industries to avoid", ", ".join(profile.industries_avoid),
                                  placeholder="gambling, defense")
            stages = st.multiselect("Company stage", COMPANY_STAGES, default=profile.company_stages)
        with c4:
            languages = st.text_input("Languages", ", ".join(profile.languages),
                                      placeholder="English (fluent), German (B1)")
            max_age = st.number_input("Ignore aggregator postings older than (days)",
                                      min_value=1, max_value=365, value=profile.max_posting_age_days)
        notes = st.text_area(
            "Anything else that matters to you", profile.notes,
            placeholder="Want to move toward ML infrastructure; prefer product companies over agencies.",
        )
        if st.form_submit_button("💾 Save profile", type="primary"):
            updated = CandidateProfile(
                target_titles=_per_line(titles), seniority=seniority,
                locations=_per_comma(locations), work_modes=work_modes,
                remote_regions=remote_regions, work_authorization=authorization,
                salary_floor=int(floor) or None, salary_currency=currency,
                must_haves=_per_line(must), dealbreakers=_per_line(deal),
                industries_prefer=_per_comma(prefer), industries_avoid=_per_comma(avoid),
                company_stages=stages, languages=_per_comma(languages),
                search_keywords=_per_comma(keywords), watchlist=profile.watchlist,
                max_posting_age_days=int(max_age), notes=notes,
            )
            try:
                save_profile(user_id, updated)
                from services.discovery import rescore

                rescore(user_id)
                st.success("Profile saved; the Discover feed is re-ranked.")
            except ProfileError as exc:
                st.error(str(exc))

    _render_watchlist(user_id)


def _render_watchlist(user_id: int) -> None:
    st.markdown("#### 🏢 Company watchlist")
    st.caption(
        "Companies whose own job boards discovery checks on every refresh: every open "
        "role, complete and current (Greenhouse, Lever, Ashby, SmartRecruiters)."
    )
    msg = st.session_state.pop("watch_msg", None)
    if msg:
        st.success(msg)
    profile = get_profile(user_id)
    for i, w in enumerate(profile.watchlist):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{w['company']}** · {w['ats']} · `{w['slug']}`")
        if c2.button("Remove", key=f"watch_rm_{i}"):
            profile.watchlist.pop(i)
            save_profile(user_id, profile)
            st.rerun()

    with st.form("watch_add_form", clear_on_submit=True):
        careers_url = st.text_input(
            "Careers page URL (most reliable)", placeholder="https://www.example.com/careers",
            help="We read which job board the page embeds (Personio, Teamtailor, Greenhouse, "
                 "Lever, Ashby, Workable, Recruitee, Pinpoint, Softgarden, SmartRecruiters).",
        )
        c1, c2, c3 = st.columns([3, 2, 2])
        company = c1.text_input("Company", placeholder="IQM")
        ats = c2.selectbox("Board", ["auto-detect", *WATCHLIST_ATS])
        slug = c3.text_input("Board slug / host (optional)", placeholder="iqm",
                             help="The company part of the jobs URL, e.g. **iqm**.teamtailor.com "
                                  "or jobs.personio.de/**acme**. For Teamtailor on a custom "
                                  "domain, the host (careers.acme.com).")
        if st.form_submit_button("➕ Add company"):
            _add_to_watchlist(user_id, company.strip(), ats, slug.strip(), careers_url.strip())

    _render_board_candidates(user_id)


def _sample_titles(ats: str, slug: str, company: str) -> str:
    from tools.job_sources import BOARD_FETCHERS, SourceError

    try:
        leads = BOARD_FETCHERS[ats](slug, company)  # cached from the probe
    except SourceError:
        return ""
    return "; ".join(f"{lead.title} ({lead.location})" if lead.location else lead.title
                     for lead in leads[:3])


def _save_watch(user_id: int, company: str, ats: str, slug: str, count: int) -> None:
    profile = get_profile(user_id)
    profile.watchlist.append({"company": company or slug, "ats": ats, "slug": slug})
    try:
        save_profile(user_id, profile)
    except ProfileError as exc:
        st.error(str(exc))
        return
    st.session_state.pop("watch_candidates", None)
    st.session_state["watch_msg"] = f"Watching {company or slug} ({ats}: {slug}), {count} open roles right now."
    st.rerun()


def _add_to_watchlist(user_id: int, company: str, ats: str, slug: str, careers_url: str) -> None:
    from tools.job_sources import BOARD_FETCHERS, SourceError, detect_board_from_page, find_boards

    if ats != "auto-detect" and slug:
        try:
            count = len(BOARD_FETCHERS[ats](slug, company or slug))
        except SourceError as exc:
            st.error(f"That board didn't respond: {exc}")
            return
        _save_watch(user_id, company, ats, slug, count)
        return
    if careers_url:
        with st.spinner("Reading the careers page…"):
            try:
                found = detect_board_from_page(careers_url, company)
            except SourceError as exc:
                st.error(f"Couldn't read that page: {exc}")
                return
        if found is None:
            st.warning(
                "No supported job board found on that page. The company may use Workday, "
                "JOIN or a custom system; use web discovery or paste postings for it."
            )
            return
        found_ats, found_slug, count = found
        _save_watch(user_id, company or found_slug, found_ats, found_slug, count)
        return
    if not company:
        st.error("Enter a careers page URL or a company name.")
        return
    with st.spinner(f"Looking for {company}'s job board…"):
        hits = find_boards(company)
    if not hits:
        st.warning(
            f"No public board answers to {company!r}. Paste their careers page URL instead; "
            "that finds boards under any name."
        )
        return
    st.session_state["watch_candidates"] = [
        {"company": company, "ats": a, "slug": sl, "count": n, "samples": _sample_titles(a, sl, company)}
        for a, sl, n in hits
    ]


def _render_board_candidates(user_id: int) -> None:
    candidates = st.session_state.get("watch_candidates") or []
    if not candidates:
        return
    st.info(
        "These boards answer to that name. A name can belong to several companies, so pick "
        "the one whose jobs look right (or paste the careers page URL instead)."
    )
    for i, c in enumerate(candidates):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{c['ats']}** · `{c['slug']}` · {c['count']} jobs  \n{c['samples'][:200]}")
        if c2.button("Add", key=f"watch_pick_{i}"):
            _save_watch(user_id, c["company"], c["ats"], c["slug"], c["count"])
    if st.button("None of these", key="watch_pick_none"):
        st.session_state.pop("watch_candidates", None)
        st.rerun()
