import os

import streamlit as st

from agents.orchestrator import run_analysis
from db.models import APPLICATION_STATUSES
from db.session import init_db
from services.applications import (
    ApplicationError,
    delete_application,
    export_applications_csv,
    export_applications_json,
    list_applications,
    save_analysis,
    update_status,
)
from services.auth import (
    AuthError,
    authenticate_user,
    change_password,
    complete_password_reset,
    register_user,
    request_password_reset,
)
from services.notifications import send_password_reset_email
from services.rate_limit import RateLimitExceeded
from tools.resume_tools import extract_resume_text
from tools.url_ingest import fetch_job_posting, is_url
from utils.config import check_environment_setup, print_environment_status

# Page config
st.set_page_config(page_title="AI Job Analysis Platform", page_icon="💼", layout="wide")

print_environment_status()
init_db()

st.title("AI Job Analysis Platform")

env_status = check_environment_setup()
if env_status["demo_mode"]:
    st.info(
        "🔄 **Demo Mode**: No LLM provider key is configured, so results use "
        "**sample data** and do not reflect the posting you submit. Set "
        "`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `FEATHERLESS_API_KEY` "
        "(see `.env.example`) to enable real analysis."
    )
else:
    st.success(f"🚀 **Production Mode**: Live analysis via **{env_status['llm_provider']}**.")


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

_RESET_GENERIC_NOTICE = (
    "If an account exists for that email, a reset token has been generated. "
    "Check your email — or, in self-hosted/demo mode, your server logs."
)


def render_auth() -> None:
    st.write("Sign in or create an account to save your analyses and track applications.")
    login_tab, register_tab, recover_tab = st.tabs(
        ["Sign in", "Create account", "Recover password"]
    )

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Sign in", type="primary"):
                try:
                    user = authenticate_user(email, password)
                    st.session_state.user_id = user.id
                    st.session_state.user_email = user.email
                    st.rerun()
                except RateLimitExceeded as exc:
                    st.error(str(exc))
                except AuthError as exc:
                    st.error(str(exc))

    with register_tab:
        with st.form("register_form"):
            email = st.text_input("Email", key="register_email")
            password = st.text_input(
                "Password",
                type="password",
                key="register_password",
                help="Minimum 8 characters.",
            )
            if st.form_submit_button("Create account", type="primary"):
                try:
                    user = register_user(email, password)
                    st.session_state.user_id = user.id
                    st.session_state.user_email = user.email
                    st.success("Account created. Signing you in...")
                    st.rerun()
                except RateLimitExceeded as exc:
                    st.error(str(exc))
                except AuthError as exc:
                    st.error(str(exc))

    with recover_tab:
        st.markdown("**Step 1 — Request a token.** It's valid for 1 hour.")
        with st.form("forgot_form"):
            forgot_email = st.text_input("Email", key="forgot_email")
            if st.form_submit_button("📧 Email me a reset token"):
                try:
                    token = request_password_reset(forgot_email)
                    if token is not None:
                        # Best-effort email delivery (no-op if SMTP unconfigured).
                        send_password_reset_email(forgot_email, token)
                        # Self-hosted operator convenience.
                        if os.getenv("RESET_TOKEN_SURFACE_IN_UI") == "1":
                            st.code(token, language=None)
                            st.caption("RESET_TOKEN_SURFACE_IN_UI=1 — disable in production.")
                        print(f"[auth] reset token for {forgot_email}: {token}")
                    # Always the same notice — never reveal whether the email exists.
                    st.info(_RESET_GENERIC_NOTICE)
                except RateLimitExceeded as exc:
                    st.error(str(exc))

        st.markdown("---")
        st.markdown("**Step 2 — Use the token to set a new password.**")
        with st.form("reset_form"):
            reset_email = st.text_input("Email", key="reset_email")
            token = st.text_input("Reset token", key="reset_token")
            new_pw = st.text_input(
                "New password", type="password", key="reset_pw", help="Minimum 8 characters."
            )
            if st.form_submit_button("Reset password", type="primary"):
                try:
                    complete_password_reset(reset_email, token, new_pw)
                    st.success("Password reset. Sign in with your new password.")
                except AuthError as exc:
                    st.error(str(exc))


if "user_id" not in st.session_state:
    render_auth()
    st.stop()


# ---------------------------------------------------------------------------
# Authenticated layout
# ---------------------------------------------------------------------------

st.sidebar.markdown(f"👤 **{st.session_state.user_email}**")

with st.sidebar.expander("🔒 Change password"):
    with st.form("change_pw_form"):
        cur_pw = st.text_input("Current password", type="password", key="cur_pw")
        new_pw = st.text_input("New password", type="password", key="new_pw", help="Minimum 8 characters.")
        if st.form_submit_button("Update password"):
            try:
                change_password(st.session_state.user_id, cur_pw, new_pw)
                st.success("Password updated.")
            except AuthError as exc:
                st.error(str(exc))

if os.getenv("TELEGRAM_BOT_USERNAME"):
    bot_url = f"https://t.me/{os.getenv('TELEGRAM_BOT_USERNAME').lstrip('@')}"
    st.sidebar.markdown(f"💬 [Use the Telegram bot]({bot_url})")

st.sidebar.title("About")
st.sidebar.info(
    "AI-assisted analysis of job postings: requirements, company signals, "
    "compensation and cost-of-living context, plus a final recommendation. "
    "Upload a resume to also get an ATS match score and gap analysis."
)
if env_status["demo_mode"]:
    st.sidebar.warning(
        "**Demo mode active** — every section below is sample output, not a "
        "real assessment of your posting."
    )
else:
    st.sidebar.caption(
        f"LLM provider: `{env_status['llm_provider']}`. Live data sources are "
        "active where configured; figures from any unconfigured source are "
        "labelled ESTIMATE in-report."
    )

# Sign out lives at the bottom so it isn't the second sidebar click target.
st.sidebar.markdown("---")
if st.sidebar.button("Sign out", use_container_width=True):
    for key in ("user_id", "user_email", "last_result", "last_inputs"):
        st.session_state.pop(key, None)
    st.rerun()

analyze_tab, applications_tab = st.tabs(["🔍 Analyze a posting", "📌 My Applications"])


# ---------------------------------------------------------------------------
# Analyze tab
# ---------------------------------------------------------------------------

def render_result(result: dict) -> None:
    verdict = result.get("verdict") or {}
    if verdict:
        light = verdict.get("light", "yellow")
        label = verdict.get("verdict", "Consider with Caution")
        confidence = verdict.get("confidence")
        badge_text = f"**Verdict:** {label}"
        if confidence is not None:
            badge_text += f" · Confidence {confidence}/10"
        if verdict.get("source") == "inferred":
            badge_text += " _(inferred — model didn't return structured verdict)_"
        {"green": st.success, "red": st.error}.get(light, st.warning)(badge_text)
        for reason in verdict.get("reasons", []) or []:
            st.write(f"- {reason}")

    with st.expander("🎯 Job Analysis", expanded=True):
        st.write(result["job_details"])
        requirements = result["job_details"].get("requirements_analysis", {})
        tech_skills = requirements.get("technical_skills", []) if isinstance(requirements, dict) else []
        if tech_skills:
            st.subheader("Key Skills Required")
            for skill in tech_skills:
                st.write(f"- {skill}")

    with st.expander("🏢 Company Research", expanded=True):
        st.write(result["company_analysis"])

    with st.expander("💰 Compensation Analysis", expanded=True):
        st.write(result["salary_analysis"])

    resume_analysis = result.get("resume_analysis") or {}
    if resume_analysis:
        with st.expander("📄 Resume / ATS Analysis", expanded=True):
            score = resume_analysis.get("ats_score", 0)
            matched = resume_analysis.get("matched_skills", []) or []
            missing = resume_analysis.get("missing_skills", []) or []
            issues = resume_analysis.get("format_issues") or []

            # Top row: big score + counts. Color the score for at-a-glance read.
            score_col, m_col, g_col = st.columns([1, 1, 1])
            with score_col:
                st.metric("ATS keyword match", f"{score}/100")
                if score >= 75:
                    st.success("Strong keyword overlap.")
                elif score >= 50:
                    st.warning("Decent overlap — close the gaps below.")
                else:
                    st.error("Low overlap — significant gap to close.")
            with m_col:
                st.metric("Matched skills", len(matched))
            with g_col:
                st.metric("Missing skills", len(missing), delta=None)

            sk_col_m, sk_col_g = st.columns(2)
            with sk_col_m:
                st.markdown("**✅ Matched required skills**")
                if matched:
                    for s in matched:
                        st.markdown(f"<span style='color:#22c55e'>✓</span> {s}", unsafe_allow_html=True)
                else:
                    st.caption("_None matched — review the missing list._")
            with sk_col_g:
                st.markdown("**❌ Missing required skills**")
                if missing:
                    for s in missing:
                        st.markdown(f"<span style='color:#ef4444'>✗</span> {s}", unsafe_allow_html=True)
                else:
                    st.caption("_All required skills detected._")

            if issues:
                st.markdown("**⚠️ ATS formatting issues**")
                for i in issues:
                    st.write(f"- {i}")
            st.markdown("---")
            st.markdown(resume_analysis.get("commentary", ""))

    st.subheader("📑 Final Recommendation")
    st.markdown(result["final_report"])


with analyze_tab:
    st.caption(
        "Paste a job description (or a URL we can fetch), optionally drop in "
        "your resume, and get a verdict in under a minute. Company / title / "
        "location are auto-extracted — only fill them below if extraction misses."
    )

    with st.form("job_analysis_form"):
        model_choice = st.radio(
            "Analysis depth",
            ["Fast", "Detailed"],
            index=0,
            help="Fast = quicker, cheaper model. Detailed = deeper reasoning model.",
            horizontal=True,
        )

        st.markdown("**1. The posting**")
        url_tab, paste_tab = st.tabs(["🔗 From URL", "📝 Paste text"])
        with url_tab:
            job_url = st.text_input(
                "Job posting URL",
                help=(
                    "JS-heavy sites (LinkedIn / Indeed / Glassdoor) often need "
                    "the headless-browser fallback (BROWSER_SCRAPER_ENABLED=1). "
                    "If a URL doesn't fetch cleanly, switch to Paste text."
                ),
            )
        with paste_tab:
            job_description = st.text_area(
                "Job description",
                height=240,
                help="Paste the complete posting text here.",
            )

        st.markdown("**2. Your resume** _(optional — enables ATS match score)_")
        resume_file = st.file_uploader(
            "Resume — PDF, DOCX, or TXT",
            type=["pdf", "docx", "txt", "md"],
            label_visibility="collapsed",
        )

        with st.expander("**3. Override extracted details** _(optional)_"):
            st.caption(
                "Leave blank to use whatever the analyzer extracts from the "
                "posting. Fill these in only if extraction gets it wrong."
            )
            col1, col2 = st.columns(2)
            with col1:
                company_name = st.text_input("Company name")
                job_title = st.text_input("Job title")
            with col2:
                location = st.text_input("Location")
                compensation = st.text_input(
                    "Compensation as listed",
                    help="What the posting itself says — leave blank if unspecified.",
                )

        analyze_submitted = st.form_submit_button("🔍 Analyze posting", type="primary")

    if analyze_submitted:
        posting_text = job_description or ""
        if job_url and is_url(job_url):
            with st.spinner(f"Fetching {job_url} ..."):
                try:
                    posting_text = fetch_job_posting(job_url)
                    st.success(f"Fetched {len(posting_text)} characters from URL.")
                except ValueError as exc:
                    st.error(str(exc))
                    posting_text = job_description or ""

        resume_text = ""
        if resume_file is not None:
            try:
                resume_text = extract_resume_text(resume_file.getvalue(), resume_file.name)
                if not resume_text.strip():
                    st.warning("Resume parsed but contained no extractable text — skipping ATS.")
            except ValueError as exc:
                st.error(f"Resume parsing failed: {exc}")

        if not posting_text:
            st.warning(
                "We need either a fetchable URL or pasted job-description text "
                "to start. Add one of those and try again."
            )
        else:
            # Manual fields are now hints, not requirements — extraction fills the gaps.
            job_data = {
                "company_name": company_name,
                "job_title": job_title,
                "location": location,
                "compensation": compensation,
            }
            selected_model = "detailed" if "Detailed" in model_choice else "fast"

            # Quota check — runs BEFORE the LLM does any work.
            from services.analysis_runner import check_user_quota
            from services.rate_limit import RateLimitExceeded
            try:
                check_user_quota(st.session_state.user_id)
            except RateLimitExceeded as exc:
                st.error(str(exc))
                st.stop()

            st.subheader("Analysis Progress")
            progress_bar = st.progress(0)
            status_text = st.empty()
            status_text.text("Starting analysis...")
            tool_findings = st.container()

            def update_progress(stage, progress, stage_info=None):
                if stage == "job":
                    status_text.text("Extracting job requirements...")
                    progress_bar.progress(25)
                elif stage == "company":
                    status_text.text("Researching company information...")
                    progress_bar.progress(50)
                elif stage == "salary":
                    status_text.text("Analyzing compensation and cost of living...")
                    progress_bar.progress(75)
                elif stage == "resume":
                    status_text.text("Running ATS / resume gap analysis...")
                    progress_bar.progress(85)
                elif stage == "report":
                    status_text.text("Generating final recommendation...")
                    progress_bar.progress(100)
                if stage_info:
                    with tool_findings:
                        st.info(f"**{stage.title()}:** {stage_info}")

            with st.spinner("Analyzing job posting..."):
                result = run_analysis(
                    posting_text,
                    job_data,
                    selected_model,
                    progress_callback=update_progress,
                    resume_text=resume_text,
                )

            if result.get("error"):
                st.error(f"Analysis failed: {result['error']}")
            else:
                status_text.text("Analysis completed!")
                progress_bar.progress(100)
                st.success("Analysis completed!")
                # If the user left manual fields blank, fall back to extraction so
                # Save (which requires company + title) doesn't fail downstream.
                extracted = (result.get("job_details") or {}).get("extracted_details") or {}
                for field in ("company_name", "job_title", "location", "compensation"):
                    if not job_data.get(field):
                        val = extracted.get(field) if isinstance(extracted, dict) else None
                        job_data[field] = val or ""
                st.session_state.last_result = result
                st.session_state.last_inputs = job_data

    # Render the last result (survives the rerun after Save).
    if st.session_state.get("last_result"):
        render_result(st.session_state.last_result)

        with st.form("save_application_form"):
            st.markdown("### Save to applications")
            col_s, col_n = st.columns([1, 3])
            with col_s:
                save_status = st.selectbox("Status", APPLICATION_STATUSES, index=0)
            with col_n:
                save_notes = st.text_input("Notes (optional)", "")
            if st.form_submit_button("💾 Save analysis", type="primary"):
                try:
                    save_analysis(
                        user_id=st.session_state.user_id,
                        manual_inputs=st.session_state.last_inputs,
                        analysis_result=st.session_state.last_result,
                        status=save_status,
                        notes=save_notes or None,
                    )
                    st.success("Saved to My Applications.")
                except ApplicationError as exc:
                    st.error(str(exc))


# ---------------------------------------------------------------------------
# My Applications tab
# ---------------------------------------------------------------------------

with applications_tab:
    records = list_applications(st.session_state.user_id)

    # Empty state — give new users a clear next step instead of a blank page.
    if not records:
        st.markdown(
            """
            ### 📭 No saved applications yet

            When you run an analysis on the **🔍 Analyze a posting** tab and
            click **💾 Save analysis**, it'll show up here. From there you can:

            - Track status (saved → applied → interviewing → offer / rejected)
            - Add notes per application
            - Export everything to CSV or JSON for backup
            - Re-view any saved report without a fresh LLM call
            """
        )
    else:
        # Filter / search bar. Defaults show everything; we don't hide-by-default.
        f_col1, f_col2 = st.columns([2, 1])
        with f_col1:
            search = st.text_input(
                "Search",
                placeholder="Filter by company, title, location, or note…",
                label_visibility="collapsed",
            ).strip().lower()
        with f_col2:
            status_filter = st.selectbox(
                "Status",
                ["All statuses"] + list(APPLICATION_STATUSES),
                label_visibility="collapsed",
            )

        def _matches(rec) -> bool:
            if status_filter != "All statuses" and rec.status != status_filter:
                return False
            if not search:
                return True
            haystack = " ".join(
                str(x or "").lower() for x in (
                    rec.company_name, rec.job_title, rec.location, rec.notes, rec.verdict
                )
            )
            return search in haystack

        visible = [r for r in records if _matches(r)]
        st.caption(
            f"Showing **{len(visible)}** of **{len(records)}** saved application(s)."
        )

        col_csv, col_json = st.columns(2)
        with col_csv:
            st.download_button(
                "⬇️ Export CSV",
                data=export_applications_csv(st.session_state.user_id),
                file_name="applications.csv",
                mime="text/csv",
            )
        with col_json:
            st.download_button(
                "⬇️ Export JSON (full backup)",
                data=export_applications_json(st.session_state.user_id),
                file_name="applications.json",
                mime="application/json",
            )

        for rec in visible:
            light = rec.verdict_light or "yellow"
            light_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(light, "⚪")
            ats = f" · ATS {rec.ats_score}/100" if rec.ats_score is not None else ""
            header = (
                f"{light_emoji} **{rec.job_title}** @ {rec.company_name} "
                f"· _{rec.status}_{ats}"
            )
            with st.expander(header):
                col_meta, col_actions = st.columns([3, 1])
                with col_meta:
                    st.write(f"**Location:** {rec.location or '—'}")
                    st.write(f"**Verdict:** {rec.verdict or '—'}")
                    st.write(f"**Saved:** {rec.created_at.strftime('%Y-%m-%d %H:%M')}")
                    if rec.notes:
                        st.write(f"**Notes:** {rec.notes}")
                with col_actions:
                    # Two-step delete: first click arms, second confirms.
                    armed_key = f"del_armed_{rec.id}"
                    if st.session_state.get(armed_key):
                        st.warning("Delete this saved analysis?")
                        col_y, col_n = st.columns(2)
                        if col_y.button("Yes, delete", key=f"del_yes_{rec.id}", type="primary"):
                            delete_application(st.session_state.user_id, rec.id)
                            st.session_state.pop(armed_key, None)
                            st.rerun()
                        if col_n.button("Cancel", key=f"del_no_{rec.id}"):
                            st.session_state.pop(armed_key, None)
                            st.rerun()
                    else:
                        if st.button("🗑️ Delete", key=f"del_{rec.id}"):
                            st.session_state[armed_key] = True
                            st.rerun()

                with st.form(f"update_{rec.id}"):
                    new_status = st.selectbox(
                        "Update status",
                        APPLICATION_STATUSES,
                        index=APPLICATION_STATUSES.index(rec.status),
                        key=f"status_{rec.id}",
                    )
                    new_notes = st.text_area("Notes", rec.notes or "", key=f"notes_{rec.id}")
                    if st.form_submit_button("Update"):
                        try:
                            update_status(
                                st.session_state.user_id,
                                rec.id,
                                status=new_status,
                                notes=new_notes or None,
                            )
                            st.success("Updated.")
                            st.rerun()
                        except ApplicationError as exc:
                            st.error(str(exc))

                report = rec.analysis_json.get("final_report")
                if report:
                    with st.expander("📑 Saved report"):
                        st.markdown(report)

        if records and not visible:
            st.info("No saved applications match the current filter.")
