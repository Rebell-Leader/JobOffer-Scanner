import os

import streamlit as st

from agents.orchestrator import run_analysis
from db.models import APPLICATION_STATUSES
from db.session import init_db
from services.applications import (
    ApplicationError,
    delete_application,
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
    login_tab, register_tab, forgot_tab, reset_tab = st.tabs(
        ["Sign in", "Create account", "Forgot password", "Use reset token"]
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
                except AuthError as exc:
                    st.error(str(exc))

    with forgot_tab:
        with st.form("forgot_form"):
            st.write(
                "We'll generate a one-shot reset token (valid for 1 hour). "
                "Email delivery is intentionally not bundled — wire SMTP/SES "
                "in your environment, or read the token from server logs for "
                "self-hosted deployments."
            )
            email = st.text_input("Email", key="forgot_email")
            if st.form_submit_button("Request reset token", type="primary"):
                token = request_password_reset(email)
                if token is not None:
                    # Self-hosted convenience: surface the token so a single
                    # operator can complete the flow without email. Real
                    # multi-tenant deployments must NOT keep this branch.
                    if os.getenv("RESET_TOKEN_SURFACE_IN_UI") == "1":
                        st.code(token, language=None)
                        st.caption("RESET_TOKEN_SURFACE_IN_UI=1 — disable in production.")
                    print(f"[auth] reset token for {email}: {token}")
                # Always show the same notice — never reveal whether the email exists.
                st.info(_RESET_GENERIC_NOTICE)

    with reset_tab:
        with st.form("reset_form"):
            email = st.text_input("Email", key="reset_email")
            token = st.text_input("Reset token", key="reset_token")
            new_pw = st.text_input(
                "New password", type="password", key="reset_pw", help="Minimum 8 characters."
            )
            if st.form_submit_button("Reset password", type="primary"):
                try:
                    complete_password_reset(email, token, new_pw)
                    st.success("Password reset. Sign in with your new password.")
                except AuthError as exc:
                    st.error(str(exc))


if "user_id" not in st.session_state:
    render_auth()
    st.stop()


# ---------------------------------------------------------------------------
# Authenticated layout
# ---------------------------------------------------------------------------

st.sidebar.markdown(f"**Signed in as:** {st.session_state.user_email}")
if st.sidebar.button("Sign out"):
    for key in ("user_id", "user_email", "last_result", "last_inputs"):
        st.session_state.pop(key, None)
    st.rerun()

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
            st.metric("ATS keyword match", f"{resume_analysis.get('ats_score', 0)}/100")
            col_m, col_g = st.columns(2)
            with col_m:
                st.write("**Matched required skills**")
                for s in resume_analysis.get("matched_skills", []) or ["_none_"]:
                    st.write(f"- {s}")
            with col_g:
                st.write("**Missing required skills**")
                for s in resume_analysis.get("missing_skills", []) or ["_none_"]:
                    st.write(f"- {s}")
            issues = resume_analysis.get("format_issues") or []
            if issues:
                st.write("**ATS formatting issues**")
                for i in issues:
                    st.write(f"- {i}")
            st.markdown("---")
            st.markdown(resume_analysis.get("commentary", ""))

    st.subheader("📑 Final Recommendation")
    st.markdown(result["final_report"])


with analyze_tab:
    with st.form("job_analysis_form"):
        model_choice = st.radio(
            "Select analysis depth:",
            ["Fast", "Detailed"],
            index=0,
            help="Fast = quicker, cheaper model. Detailed = deeper reasoning model.",
        )
        col1, col2 = st.columns(2)
        with col1:
            company_name = st.text_input("Company Name*")
            job_title = st.text_input("Job Title*")
        with col2:
            location = st.text_input("Location*")
            compensation = st.text_input("Compensation")
        job_url = st.text_input(
            "Job posting URL (optional)",
            help=(
                "If provided, we'll fetch the page text. JS-heavy sites "
                "(LinkedIn / Indeed / Glassdoor) often won't work — paste below instead."
            ),
        )
        job_description = st.text_area(
            "Full Job Description*",
            height=240,
            help="Paste the complete job posting text here (used if no URL is provided).",
        )
        resume_file = st.file_uploader(
            "Resume (optional — PDF, DOCX, or TXT)",
            type=["pdf", "docx", "txt", "md"],
        )
        analyze_submitted = st.form_submit_button("Analyze Job", type="primary")

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

        if posting_text and company_name and job_title and location:
            job_data = {
                "company_name": company_name,
                "job_title": job_title,
                "location": location,
                "compensation": compensation,
            }
            selected_model = "detailed" if "Detailed" in model_choice else "fast"

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
                # Persist in session so a rerun (after Save) doesn't wipe it.
                st.session_state.last_result = result
                st.session_state.last_inputs = job_data
        else:
            st.warning("Please provide a posting URL or paste the description, plus the required text fields (*).")

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
    if not records:
        st.write("No saved applications yet. Run an analysis and click **💾 Save analysis**.")
    else:
        st.write(f"You have **{len(records)}** saved application(s).")
        for rec in records:
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
                    if st.button("🗑️ Delete", key=f"del_{rec.id}"):
                        delete_application(st.session_state.user_id, rec.id)
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
