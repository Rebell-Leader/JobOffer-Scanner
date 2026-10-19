"""Discover tab: the personalised job feed."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List

import streamlit as st

from services import discovery
from services.discovery import DiscoveryError, JobRecord
from services.profile import get_profile
from tools.job_sources import AGGREGATORS, adzuna_available
from utils.llm import is_demo_mode, web_research_available

_SOURCE_LABELS: Dict[str, str] = {
    "watchlist": "My company watchlist",
    **{k: v["label"] for k, v in AGGREGATORS.items()},
}


def _badge(score) -> str:
    if score is None:
        return "⚪ –"
    return f"{'🟢' if score >= 75 else '🟡' if score >= 50 else '🔴'} {score}"


def _age(posted: datetime | None) -> str:
    if posted is None:
        return ""
    days = (datetime.utcnow() - posted).days
    return "today" if days <= 0 else f"{days}d ago"


def _profile_line(user_id: int) -> str:
    p = get_profile(user_id)
    parts = [
        ", ".join(p.target_titles) or ", ".join(p.search_keywords),
        ", ".join(p.locations),
        "/".join(p.work_modes),
        f"≥ {p.salary_floor:,} {p.salary_currency}" if p.salary_floor else "",
        f"{len(p.watchlist)} watched companies" if p.watchlist else "",
    ]
    return " · ".join(x for x in parts if x)


def render_discover_tab(user_id: int, render_result: Callable[[dict], None], personal: bool) -> None:
    st.header("🔭 Discover jobs")
    profile = get_profile(user_id)
    if profile.is_empty() and not profile.watchlist:
        st.info(
            "Tell me what you're looking for first: **📝 CV & Profile → 👤 Search profile** "
            "(target roles, locations, salary floor, dealbreakers, companies to watch). "
            "*Draft from my master CV* fills most of it for you."
        )
        return
    st.caption(f"Looking for: {_profile_line(user_id)}")

    options = list(discovery.DEFAULT_SOURCES) + (["adzuna"] if adzuna_available() else [])
    sources = st.multiselect(
        "Sources", options, default=list(discovery.DEFAULT_SOURCES),
        format_func=lambda s: _SOURCE_LABELS.get(s, s), key="discover_sources",
    )
    c1, c2, c3 = st.columns(3)
    if c1.button("🔄 Refresh feed", type="primary", key="discover_refresh"):
        status = st.status("Refreshing…", expanded=False)
        try:
            report = discovery.refresh_feed(user_id, sources=sources, progress=status.write)
            st.session_state["discover_report"] = report
            status.update(label=f"Found {report.new} new matching jobs", state="complete")
        except DiscoveryError as exc:
            status.update(label=str(exc), state="error")
    if web_research_available():
        if c2.button("🌐 Search the web with Claude", key="discover_web",
                     help="Claude searches employer career pages and ATS boards beyond the "
                          "feed sources (about $0.10–0.30 per run)."):
            with st.spinner("Searching the web…"):
                try:
                    report = discovery.web_discover(user_id)
                    st.session_state["discover_report"] = report
                except Exception as exc:  # noqa: BLE001 - provider/search failure
                    st.error(f"Web search failed: {exc}")
    if not is_demo_mode() and c3.button("🎯 Score unscored jobs", key="discover_triage"):
        with st.spinner("Scoring fit…"):
            n = discovery.triage(user_id, limit=60)
        st.success(f"Scored {n} jobs.")

    last = st.session_state.get("discover_report")
    if last is not None:
        st.caption(
            f"Last refresh: {last.fetched} fetched → {last.kept} matched your profile → "
            f"{last.new} new, {last.seen_again} already in your feed; {last.triaged} scored."
        )
        if last.errors:
            st.warning("Some sources failed: " + "; ".join(f"{k}: {v}" for k, v in last.errors.items()))

    counts = discovery.feed_counts(user_id)
    f1, f2, f3, f4 = st.columns([2, 2, 1, 2])
    status_choice = f1.radio(
        "Show", ["new", "saved", "dismissed"], horizontal=True, key="discover_status",
        format_func=lambda s: f"{s.title()} ({counts.get(s, 0)})",
    )
    min_score = f2.slider("Minimum score", 0, 100, 0, step=5, key="discover_min")
    remote_only = f3.checkbox("Remote only", key="discover_remote")
    text = f4.text_input("Filter", key="discover_text", placeholder="company, title, city…").lower()

    jobs = discovery.list_feed(user_id, status=status_choice)
    shown: List[JobRecord] = [
        j for j in jobs
        if ((j.fit_score if j.fit_score is not None else j.match_score) or 0) >= min_score
        and (not remote_only or j.remote)
        and (not text or text in f"{j.title} {j.company} {j.location}".lower())
    ]
    if not shown:
        st.info("Nothing here yet. Refresh the feed, or loosen the filters.")
        return
    if is_demo_mode():
        st.caption("No API key set: ranking is the deterministic match score only.")
    for job in shown[:50]:
        _render_job(user_id, job, render_result, personal)
    if len(shown) > 50:
        st.caption(f"…and {len(shown) - 50} more. Raise the minimum score to narrow it down.")


def _render_job(user_id: int, job: JobRecord, render_result: Callable[[dict], None], personal: bool) -> None:
    with st.container(border=True):
        score = job.fit_score if job.fit_score is not None else job.match_score
        label = "fit" if job.fit_score is not None else "match"
        safe_title = job.title.replace("[", "(").replace("]", ")")
        title = f"[{safe_title}]({job.url})" if job.url else safe_title
        meta = " · ".join(x for x in (
            job.company, job.location, "🌍 remote" if job.remote else "", _age(job.posted_at),
            job.salary_text, f"via {_SOURCE_LABELS.get(job.source, job.source)}",
        ) if x)
        st.markdown(f"**{_badge(score)}** ({label}) · **{title}**")
        st.caption(meta)
        if job.fit_summary:
            st.markdown(f"_{job.fit_summary}_")
        if job.fit_flags:
            st.caption("⚠️ " + " · ".join(job.fit_flags))

        b1, b2, b3, _ = st.columns([2, 2, 2, 3])
        if job.status != "saved" and b1.button("🔍 Deep analysis", key=f"disc_an_{job.id}"):
            _deep_analysis(user_id, job, personal)
        if job.status == "new" and b2.button("📌 Track", key=f"disc_tr_{job.id}"):
            discovery.track(user_id, job.id)
            st.rerun()
        if job.status == "new" and b3.button("✖ Dismiss", key=f"disc_dm_{job.id}"):
            discovery.set_status(user_id, job.id, "dismissed")
            st.rerun()
        if job.status == "dismissed" and b3.button("↩ Restore", key=f"disc_rs_{job.id}"):
            discovery.set_status(user_id, job.id, "new")
            st.rerun()
        if job.status == "saved":
            b1.caption("In **📌 My Applications**")

        if job.description and st.toggle("Show posting", key=f"disc_desc_{job.id}"):
            st.text(job.description[:6000])

        result = st.session_state.get("disc_results", {}).get(job.id)
        if result:
            if result.get("error"):
                st.error(f"Analysis failed: {result['error']}")
            else:
                render_result(result)
                if st.button("💾 Save analysis to My Applications", key=f"disc_sv_{job.id}",
                             type="primary"):
                    discovery.track(user_id, job.id, analysis_result=result)
                    st.session_state["disc_results"].pop(job.id, None)
                    st.rerun()


def _deep_analysis(user_id: int, job: JobRecord, personal: bool) -> None:
    from services.analysis_runner import check_user_quota, run_analysis_sync
    from services.master_cv import get_master_cv

    if not personal:
        try:
            check_user_quota(user_id)
        except Exception as exc:  # noqa: BLE001 - quota / budget / tier message
            st.error(str(exc))
            return
    with st.spinner("Deep analysis with live research: usually 1–3 minutes…"):
        posting = discovery.posting_text_for(user_id, job.id)
        cv = get_master_cv(user_id)
        result = run_analysis_sync(
            posting,
            manual_inputs={"company_name": job.company, "job_title": job.title,
                           "location": job.location, "compensation": job.salary_text},
            model="detailed",
            resume_text=cv.raw_text if cv else None,
            user_id=user_id,
        )
    st.session_state.setdefault("disc_results", {})[job.id] = result
