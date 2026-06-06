import os

import streamlit as st

from agents.orchestrator import run_analysis
from db.models import APPLICATION_STATUSES
from db.session import init_db
from services.analytics import compute_dashboard
from services.applications import (
    ApplicationError,
    delete_application,
    export_applications_csv,
    export_applications_json,
    list_applications,
    save_analysis,
    update_status,
)
from services.master_cv import (
    MasterCVError,
    delete_master_cv,
    get_master_cv,
    parse_master_cv,
    save_master_cv,
    save_master_cv_from_upload,
)
from services.projects import (
    ProjectError,
    create_project,
    delete_project,
    list_projects,
    update_project,
)
from services.stages import (
    QUICK_ACTIONS,
    StageError,
    add_stage,
    delete_stage,
    list_stages,
)
from services.constraint_check import ConstraintCheck, summarize as summarize_check
from services.tailoring import (
    COVER_LETTER_TONES,
    TailoringError,
    delete_artifact,
    generate_cover_letter,
    generate_tailored_cv,
    list_artifacts,
    recheck_artifact,
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

analyze_tab, applications_tab, analytics_tab, cv_tab = st.tabs(
    ["🔍 Analyze a posting", "📌 My Applications", "📊 Analytics", "📝 CV & Projects"]
)


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

                # ----- Pipeline stages -----
                st.markdown("##### 🪜 Pipeline stages")
                stages = list_stages(st.session_state.user_id, rec.id)
                if stages:
                    for s in stages:
                        line = (
                            f"- **{s.occurred_on.isoformat()}** · "
                            f"`{s.kind}`"
                        )
                        if s.at_pipeline_stage:
                            line += f" _(at: {s.at_pipeline_stage})_"
                        if s.notes:
                            line += f" — {s.notes}"
                        cols = st.columns([10, 1])
                        cols[0].markdown(line)
                        if cols[1].button("🗑️", key=f"stage_del_{s.id}"):
                            try:
                                delete_stage(st.session_state.user_id, s.id)
                                st.rerun()
                            except StageError as exc:
                                st.error(str(exc))
                else:
                    st.caption("_No stages yet — record what happened next:_")

                # Quick-action buttons (one-click stage adds with today's date).
                qa_cols = st.columns(3)
                for idx, (label, kind) in enumerate(QUICK_ACTIONS):
                    if qa_cols[idx % 3].button(label, key=f"qa_{rec.id}_{kind}"):
                        try:
                            add_stage(st.session_state.user_id, rec.id, kind=kind)
                            st.rerun()
                        except StageError as exc:
                            st.error(str(exc))

                # Detailed add-stage form (date + notes + at_pipeline_stage).
                with st.expander("➕ Add a stage with details"):
                    with st.form(f"stage_form_{rec.id}"):
                        stage_kind = st.selectbox(
                            "Stage",
                            [k for _, k in QUICK_ACTIONS],
                            key=f"stage_kind_{rec.id}",
                        )
                        stage_date = st.date_input("When", key=f"stage_date_{rec.id}")
                        at_pipeline = st.selectbox(
                            "If terminal (rejected/withdrew/ghosted): at which pipeline stage?",
                            ["(none)"] + list(
                                __import__("db.models", fromlist=["PIPELINE_STAGES"]).PIPELINE_STAGES
                            ),
                            key=f"stage_at_{rec.id}",
                        )
                        stage_notes = st.text_area(
                            "Notes / feedback",
                            placeholder="Verbatim feedback, offer details, why it stalled, etc.",
                            key=f"stage_notes_{rec.id}",
                        )
                        if st.form_submit_button("Add stage", type="primary"):
                            try:
                                add_stage(
                                    st.session_state.user_id,
                                    rec.id,
                                    kind=stage_kind,
                                    occurred_on=stage_date,
                                    notes=stage_notes or None,
                                    at_pipeline_stage=(
                                        None if at_pipeline == "(none)" else at_pipeline
                                    ),
                                )
                                st.success("Stage added.")
                                st.rerun()
                            except StageError as exc:
                                st.error(str(exc))

                # ----- Tailored artifacts (CV / cover letter) -----
                st.markdown("##### 🎯 Tailored artifacts")
                master_cv_present = get_master_cv(st.session_state.user_id) is not None
                if not master_cv_present:
                    st.caption(
                        "_Add a master CV in the **📝 CV & Projects** tab to "
                        "enable tailored CV / cover letter generation. We use "
                        "only facts from your master CV — nothing is invented._"
                    )
                else:
                    art_col1, art_col2 = st.columns(2)
                    with art_col1:
                        if st.button("📄 Generate tailored CV", key=f"gen_cv_{rec.id}"):
                            with st.spinner("Tailoring CV from your master CV…"):
                                try:
                                    art = generate_tailored_cv(
                                        st.session_state.user_id, rec.id
                                    )
                                    st.success(f"Saved tailored CV #{art.id}.")
                                    st.rerun()
                                except (TailoringError, MasterCVError) as exc:
                                    st.error(str(exc))
                    with art_col2:
                        # Tone preset for cover letters — persists per-application
                        # in session_state so the user's last choice sticks.
                        tone_key = f"cl_tone_{rec.id}"
                        tone = st.selectbox(
                            "Cover letter tone",
                            COVER_LETTER_TONES,
                            index=0,
                            key=tone_key,
                            label_visibility="collapsed",
                        )
                        if st.button(
                            f"✉️ Generate cover letter ({tone})", key=f"gen_cl_{rec.id}"
                        ):
                            with st.spinner("Writing cover letter from your master CV…"):
                                try:
                                    art = generate_cover_letter(
                                        st.session_state.user_id, rec.id, tone=tone
                                    )
                                    st.success(f"Saved cover letter #{art.id}.")
                                    st.rerun()
                                except (TailoringError, MasterCVError) as exc:
                                    st.error(str(exc))

                # List existing artifacts (newest first).
                arts = list_artifacts(st.session_state.user_id, rec.id)
                if arts:
                    for a in arts:
                        icon = "📄" if a.kind == "tailored_cv" else "✉️"
                        label = "Tailored CV" if a.kind == "tailored_cv" else "Cover letter"
                        check = ConstraintCheck.from_dict((a.meta or {}).get("constraint_check"))
                        check_emoji = "✅" if check.is_clean else "⚠️"
                        header = (
                            f"{icon} {label} #{a.id} · "
                            f"{a.created_at.strftime('%Y-%m-%d %H:%M')} · {check_emoji}"
                        )
                        with st.expander(header):
                            # Constraint-check badge — the whole point of the post-check.
                            if check.is_clean:
                                st.success(summarize_check(check))
                            else:
                                st.warning(summarize_check(check))
                                bits = []
                                if check.new_proper_nouns:
                                    bits.append(
                                        ("**New skill/term tokens not in your master CV / projects:** "
                                         + ", ".join(f"`{t}`" for t in check.new_proper_nouns))
                                    )
                                if check.new_years:
                                    bits.append(
                                        "**New years:** " + ", ".join(f"`{y}`" for y in check.new_years)
                                    )
                                if check.new_percentages:
                                    bits.append(
                                        "**New percentages:** " + ", ".join(
                                            f"`{p}`" for p in check.new_percentages
                                        )
                                    )
                                if check.new_quantitative_claims:
                                    bits.append(
                                        "**New quantitative claims:** " + ", ".join(
                                            f"`{q}`" for q in check.new_quantitative_claims
                                        )
                                    )
                                for bit in bits:
                                    st.markdown(bit)
                                st.caption(
                                    "False positives are possible (the detector is "
                                    "case-folded substring matching, not semantic). "
                                    "If a flagged term is genuinely in your master CV, "
                                    "edit it there and click **Re-check** below."
                                )

                            st.markdown("---")
                            st.markdown(a.content)
                            a_btns = st.columns([1, 1, 3])
                            with a_btns[0]:
                                if st.button("🗑️", key=f"art_del_{a.id}"):
                                    try:
                                        delete_artifact(st.session_state.user_id, a.id)
                                        st.rerun()
                                    except TailoringError as exc:
                                        st.error(str(exc))
                            with a_btns[1]:
                                if st.button("🔁 Re-check", key=f"art_recheck_{a.id}"):
                                    try:
                                        recheck_artifact(st.session_state.user_id, a.id)
                                        st.rerun()
                                    except TailoringError as exc:
                                        st.error(str(exc))
                            with a_btns[2]:
                                st.download_button(
                                    "⬇️ Download .md",
                                    data=a.content,
                                    file_name=f"{a.kind}_{rec.company_name}_{a.id}.md",
                                    mime="text/markdown",
                                    key=f"art_dl_{a.id}",
                                )

                report = rec.analysis_json.get("final_report")
                if report:
                    with st.expander("📑 Saved report"):
                        st.markdown(report)

        if records and not visible:
            st.info("No saved applications match the current filter.")


# ---------------------------------------------------------------------------
# Analytics tab
# ---------------------------------------------------------------------------

with analytics_tab:
    dash = compute_dashboard(st.session_state.user_id)
    o = dash.overview

    if o.total_applications == 0:
        st.markdown(
            """
            ### 📊 Nothing to chart yet

            Analytics light up as you save applications and add stage events
            (Applied → screens → interviews → offer/reject). The funnel,
            conversion rates, time-in-stage averages, and verdict-vs-outcome
            correlations all derive from those stage events — the more you
            log, the more useful this page gets.
            """
        )
    else:
        st.subheader("Overview")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total applications", o.total_applications)
        m2.metric("Active", o.active)
        m3.metric("In interview", o.in_interview)
        m4.metric("Offers received", o.offers_received)
        m5, m6, m7, m8 = st.columns(4)
        m5.metric("Offers accepted", o.offers_accepted)
        m6.metric("Rejected", o.rejected)
        m7.metric("Ghosted", o.ghosted)
        m8.metric(
            "Rejection rate",
            f"{int(round(o.rejection_rate * 100))}%" if (o.rejected + o.offers_received) else "—",
            help="Rejected / (rejected + offers received) — among applications that reached a decision.",
        )

        # ----- Funnel -----
        st.subheader("Pipeline funnel")
        funnel_data = {row.stage: row.reached for row in dash.funnel if row.reached}
        if funnel_data:
            st.bar_chart(funnel_data, horizontal=True)
            conv_rows = [
                {"stage": row.stage,
                 "conversion from previous":
                     f"{int(round((row.conversion_from_previous or 0) * 100))}%"
                     if row.conversion_from_previous is not None else "—"}
                for row in dash.funnel if row.reached
            ]
            with st.expander("Stage-over-stage conversion"):
                st.table(conv_rows)
        else:
            st.caption("_No pipeline events yet — add stages on the applications tab._")

        # ----- Time in stage -----
        st.subheader("Average days between stages")
        if dash.time_in_stage:
            tis_data = {
                f"{t.from_stage} → {t.to_stage}": t.average_days
                for t in dash.time_in_stage
            }
            st.bar_chart(tis_data, horizontal=True)
            st.caption(
                "Sample sizes — "
                + ", ".join(
                    f"{t.from_stage}→{t.to_stage}: n={t.samples}"
                    for t in dash.time_in_stage
                )
            )
        else:
            st.caption("_Need at least one app with two consecutive pipeline stages dated._")

        # ----- Verdict outcomes -----
        st.subheader("Does the analyzer's verdict predict outcomes?")
        if dash.verdict_outcomes:
            st.dataframe(
                [
                    {
                        "Verdict": v.verdict,
                        "Applications": v.applications,
                        "Reached offer": v.reached_offer,
                        "Rejected": v.rejected,
                        "Offer rate": f"{int(round(v.offer_rate * 100))}%",
                    }
                    for v in dash.verdict_outcomes
                ],
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "Correlation isn't causation — but a consistent gap between "
                "Recommended-verdict offer-rate and Not-Recommended offer-rate "
                "is a signal the verdict is actually picking up something real."
            )

        # ----- Rejection stage distribution -----
        if dash.rejection_stage_distribution:
            st.subheader("Where do rejections happen?")
            st.bar_chart(dash.rejection_stage_distribution, horizontal=True)

        # ----- Volume over time -----
        if dash.volume_by_week:
            st.subheader("Applications saved per week")
            st.line_chart(dash.volume_by_week)


# ---------------------------------------------------------------------------
# CV & Projects tab
# ---------------------------------------------------------------------------

with cv_tab:
    st.caption(
        "Your **master CV** and **project gallery** are the source of truth "
        "for every tailored CV / cover letter we generate. We use only facts "
        "you've put here — nothing is invented. Update these in one place and "
        "every application can pull from them."
    )

    cv_section, proj_section = st.tabs(["📄 Master CV", "🧩 Project gallery"])

    # ----- Master CV ----------------------------------------------------------
    with cv_section:
        current_cv = get_master_cv(st.session_state.user_id)

        upload_col, paste_col = st.tabs(["⬆️ Upload (PDF/DOCX/TXT)", "📝 Paste / edit"])

        with upload_col:
            uploaded = st.file_uploader(
                "Master CV file",
                type=["pdf", "docx", "txt", "md"],
                key="master_cv_upload",
                label_visibility="collapsed",
            )
            if uploaded is not None:
                if st.button("Save uploaded CV as master", type="primary"):
                    try:
                        rec = save_master_cv_from_upload(
                            st.session_state.user_id,
                            uploaded.getvalue(),
                            uploaded.name,
                        )
                        st.success(
                            f"Master CV saved ({len(rec.raw_text)} characters)."
                        )
                        st.rerun()
                    except (MasterCVError, ValueError) as exc:
                        st.error(str(exc))

        with paste_col:
            existing = current_cv.raw_text if current_cv else ""
            with st.form("master_cv_paste"):
                pasted = st.text_area(
                    "Master CV text",
                    value=existing,
                    height=320,
                    help=(
                        "Paste your full long-form CV. The more facts you "
                        "include (skills, projects, dates, results), the more "
                        "the tailoring has to work with."
                    ),
                )
                if st.form_submit_button("Save as master CV", type="primary"):
                    try:
                        save_master_cv(st.session_state.user_id, pasted)
                        st.success("Master CV saved.")
                        st.rerun()
                    except MasterCVError as exc:
                        st.error(str(exc))

        if current_cv is not None:
            st.markdown(
                f"**Saved master CV** · {len(current_cv.raw_text)} characters · "
                f"updated {current_cv.updated_at.strftime('%Y-%m-%d %H:%M')}"
            )
            parse_col, del_col = st.columns([1, 1])
            with parse_col:
                if st.button("🧠 Parse into structured sections (optional)"):
                    with st.spinner("Asking the model to structure your CV…"):
                        try:
                            structured = parse_master_cv(st.session_state.user_id)
                            st.success("Parsed.")
                            st.json(structured)
                        except MasterCVError as exc:
                            st.error(str(exc))
            with del_col:
                armed_key = "del_master_cv_armed"
                if st.session_state.get(armed_key):
                    st.warning("Delete your master CV?")
                    yc, nc = st.columns(2)
                    if yc.button("Yes, delete", type="primary", key="del_master_yes"):
                        delete_master_cv(st.session_state.user_id)
                        st.session_state.pop(armed_key, None)
                        st.rerun()
                    if nc.button("Cancel", key="del_master_no"):
                        st.session_state.pop(armed_key, None)
                        st.rerun()
                else:
                    if st.button("🗑️ Delete master CV"):
                        st.session_state[armed_key] = True
                        st.rerun()

            if current_cv.structured:
                with st.expander("View saved structured projection"):
                    st.json(current_cv.structured)
        else:
            st.info(
                "_No master CV saved yet — upload or paste above to enable "
                "tailored CV / cover letter generation._"
            )

    # ----- Project gallery ----------------------------------------------------
    with proj_section:
        projects = list_projects(st.session_state.user_id)
        st.caption(
            f"{len(projects)} project(s) in your gallery. Tailored CVs may "
            "select and reframe these for relevance — they will not invent new ones."
        )

        with st.expander("➕ Add a project"):
            with st.form("add_project_form"):
                p_title = st.text_input("Title")
                p_role = st.text_input("Your role (optional)")
                p_tech = st.text_input("Tech stack (optional, comma-separated)")
                p_summary = st.text_area("Summary (1-2 sentences)")
                p_highlights = st.text_area(
                    "Highlights (one bullet per line)",
                    help="Concrete achievements — what you built, what changed, scale.",
                )
                p_url = st.text_input("Link (optional)")
                if st.form_submit_button("Add to gallery", type="primary"):
                    try:
                        create_project(
                            st.session_state.user_id,
                            title=p_title,
                            role=p_role,
                            tech_stack=p_tech,
                            summary=p_summary,
                            highlights=p_highlights,
                            url=p_url,
                        )
                        st.success(f"Added “{p_title}” to your gallery.")
                        st.rerun()
                    except ProjectError as exc:
                        st.error(str(exc))

        for p in projects:
            with st.expander(f"🧩 {p.title}" + (f" — {p.role}" if p.role else "")):
                if p.tech_stack:
                    st.markdown(f"**Tech:** {p.tech_stack}")
                if p.summary:
                    st.markdown(p.summary)
                if p.highlights:
                    for h in p.highlights:
                        st.markdown(f"- {h}")
                if p.url:
                    st.markdown(f"🔗 [{p.url}]({p.url})")

                with st.form(f"edit_project_{p.id}"):
                    st.markdown("**Edit**")
                    new_title = st.text_input("Title", value=p.title, key=f"pt_{p.id}")
                    new_role = st.text_input("Role", value=p.role or "", key=f"pr_{p.id}")
                    new_tech = st.text_input(
                        "Tech stack", value=p.tech_stack or "", key=f"pte_{p.id}"
                    )
                    new_summary = st.text_area(
                        "Summary", value=p.summary or "", key=f"ps_{p.id}"
                    )
                    new_highlights = st.text_area(
                        "Highlights (one per line)",
                        value="\n".join(p.highlights),
                        key=f"ph_{p.id}",
                    )
                    new_url = st.text_input("Link", value=p.url or "", key=f"pu_{p.id}")
                    if st.form_submit_button("Update"):
                        try:
                            update_project(
                                st.session_state.user_id, p.id,
                                title=new_title, role=new_role,
                                tech_stack=new_tech, summary=new_summary,
                                highlights=new_highlights, url=new_url,
                            )
                            st.success("Updated.")
                            st.rerun()
                        except ProjectError as exc:
                            st.error(str(exc))

                armed_key = f"del_proj_armed_{p.id}"
                if st.session_state.get(armed_key):
                    st.warning("Delete this project?")
                    yc, nc = st.columns(2)
                    if yc.button("Yes, delete", type="primary", key=f"dp_yes_{p.id}"):
                        delete_project(st.session_state.user_id, p.id)
                        st.session_state.pop(armed_key, None)
                        st.rerun()
                    if nc.button("Cancel", key=f"dp_no_{p.id}"):
                        st.session_state.pop(armed_key, None)
                        st.rerun()
                else:
                    if st.button("🗑️ Delete project", key=f"dp_{p.id}"):
                        st.session_state[armed_key] = True
                        st.rerun()
