import os
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from agents.orchestrator import run_analysis
from db.models import APPLICATION_STATUSES
from db.session import init_db
from services.analysis_runner import async_enabled
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
from services.background_analysis import (
    BackgroundAnalysisError,
    delete as delete_background_analysis,
    list_for_user as list_background_analyses,
    refresh_all_pending,
    submit_background_analysis,
)
from services.checkpoint import (
    CHECKPOINT_STAGES,
    compute_key as compute_checkpoint_key,
    get_store as get_checkpoint_store,
)
from services.bulk_import import (
    BulkImportError,
    parse_applications_csv,
    parse_applications_freeform,
    parse_projects_csv,
    parse_projects_freeform,
    save_applications,
    save_projects,
)
from services.master_cv import (
    MasterCVError,
    delete_master_cv,
    delete_revision,
    diff_revision_against_current,
    get_master_cv,
    list_revisions,
    parse_master_cv,
    restore_revision,
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
from services.pdf_export import PDFExportError, markdown_to_pdf
from services.suggestions import (
    apply_skill_addition,
    build_suggestions,
)
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
from services.notifications import (
    notify_stage_added,
    send_password_reset_email,
)
from services.totp import (
    TOTPError,
    confirm_setup as totp_confirm_setup,
    disable as totp_disable,
    is_enabled as totp_is_enabled,
    pending_setup as totp_pending_setup,
    remaining_backup_codes as totp_backup_codes_left,
    start_setup as totp_start_setup,
    verify_login as totp_verify_login,
)
from services.rate_limit import RateLimitExceeded
from services.reminders import (
    set_inactive_threshold,
    snooze_application,
    unsnooze_application,
)
from services.telegram_link import (
    TelegramLinkError,
    get_link,
    issue_binding_token,
    set_notify_on_stage,
    unlink,
)
from tools.resume_tools import extract_resume_text
from tools.url_ingest import fetch_job_posting, is_url
from services.timeline import (
    STAGE_COLORS,
    cross_application_swimlane,
    per_application_timeline,
    points_to_records,
)
from utils.config import check_environment_setup, print_environment_status
from utils.diff import inline_diff_html, unified_diff
from utils.logging_setup import configure as configure_logging

# Page config
st.set_page_config(page_title="AI Job Analysis Platform", page_icon="💼", layout="wide")

configure_logging()
print_environment_status()
init_db()


# ---------------------------------------------------------------------------
# Public share view — branches BEFORE the auth gate so a recipient with a
# valid token can read without an account.
# ---------------------------------------------------------------------------

share_token = st.query_params.get("share")
if share_token:
    from services.sharing import ShareError, get_view

    st.title("📄 Shared job analysis")
    try:
        view = get_view(share_token)
    except ShareError as exc:
        st.error(str(exc))
        st.stop()

    light = view.verdict_light or "yellow"
    light_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(light, "⚪")
    st.markdown(
        f"### {light_emoji} {view.job_title} @ {view.company_name}"
        + (f" — {view.location}" if view.location else "")
    )
    if view.verdict:
        st.caption(f"Verdict: **{view.verdict}** · status: _{view.status}_")
    if view.ats_score is not None:
        st.metric("ATS keyword match", f"{view.ats_score}/100")

    report = view.analysis_json.get("final_report")
    if report:
        st.markdown("---")
        st.markdown(report)
    else:
        st.info("This analysis has no report yet.")

    if view.artifacts:
        st.markdown("---")
        st.subheader("🎯 Tailored artifacts")
        for art in view.artifacts:
            label = "Tailored CV" if art["kind"] == "tailored_cv" else "Cover letter"
            with st.expander(f"{label} (created {art['created_at'][:10]})"):
                st.markdown(art["content"])

    st.caption(
        "This is a read-only shared view. Sign up to run your own analyses."
    )
    st.stop()

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
        # Two-step state: if the user passed password and is now expected to
        # provide a TOTP, render the OTP form instead of the password form.
        if st.session_state.get("pending_2fa_user_id"):
            pending_id = st.session_state["pending_2fa_user_id"]
            pending_email = st.session_state.get("pending_2fa_email", "")
            st.info(
                f"Signed into **{pending_email}**. Enter the 6-digit code "
                "from your authenticator app (or one of your backup codes) "
                "to continue."
            )
            with st.form("otp_form"):
                otp = st.text_input(
                    "Authenticator code", key="otp_input",
                    placeholder="123456 or XXXXX-XXXXX",
                )
                c1, c2 = st.columns(2)
                with c1:
                    submit_otp = st.form_submit_button("Verify", type="primary")
                with c2:
                    cancel_otp = st.form_submit_button("Cancel")
            if cancel_otp:
                st.session_state.pop("pending_2fa_user_id", None)
                st.session_state.pop("pending_2fa_email", None)
                st.rerun()
            if submit_otp:
                try:
                    if totp_verify_login(pending_id, otp):
                        st.session_state.user_id = pending_id
                        st.session_state.user_email = pending_email
                        st.session_state.pop("pending_2fa_user_id", None)
                        st.session_state.pop("pending_2fa_email", None)
                        st.rerun()
                    else:
                        st.error("That code didn't match. Try again.")
                except RateLimitExceeded as exc:
                    st.error(str(exc))
        else:
            with st.form("login_form"):
                email = st.text_input("Email", key="login_email")
                password = st.text_input(
                    "Password", type="password", key="login_password",
                )
                if st.form_submit_button("Sign in", type="primary"):
                    try:
                        user = authenticate_user(email, password)
                        if user.two_factor_required:
                            st.session_state.pending_2fa_user_id = user.id
                            st.session_state.pending_2fa_email = user.email
                            st.rerun()
                        else:
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

with st.sidebar.expander("📜 My recent activity"):
    from services.audit import list_for_user as _list_audit
    events = _list_audit(st.session_state.user_id, limit=20)
    if not events:
        st.caption("_No recorded activity yet._")
    else:
        for ev in events:
            st.caption(
                f"`{ev.created_at.strftime('%Y-%m-%d %H:%M')}` — `{ev.kind}`"
            )

with st.sidebar.expander("🔐 Two-factor authentication"):
    if totp_is_enabled(st.session_state.user_id):
        remaining = totp_backup_codes_left(st.session_state.user_id)
        st.success(f"2FA is **enabled**. {remaining} backup code(s) remaining.")
        st.caption(
            "To disable, enter your current password and confirm."
        )
        with st.form("2fa_disable_form"):
            cur_pw = st.text_input(
                "Current password", type="password", key="2fa_disable_pw",
            )
            if st.form_submit_button("Disable 2FA"):
                try:
                    totp_disable(st.session_state.user_id, cur_pw)
                    st.success("2FA disabled.")
                    st.rerun()
                except TOTPError as exc:
                    st.error(str(exc))
    else:
        # Setup ceremony lives across reruns in session_state.
        if not st.session_state.get("totp_setup_secret"):
            if st.button("Start 2FA setup"):
                try:
                    setup = totp_start_setup(
                        st.session_state.user_id,
                        st.session_state.user_email,
                    )
                    st.session_state.totp_setup_secret = setup.secret
                    st.session_state.totp_setup_uri = setup.provisioning_uri
                    st.rerun()
                except TOTPError as exc:
                    st.error(str(exc))
        else:
            try:
                import io
                import qrcode

                img = qrcode.make(st.session_state.totp_setup_uri)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                st.image(buf.getvalue(), caption="Scan in your authenticator app")
            except ImportError:
                pass
            with st.expander("Or enter the secret manually"):
                st.code(st.session_state.totp_setup_secret, language=None)
            with st.form("2fa_confirm_form"):
                otp = st.text_input("Enter the current 6-digit code")
                if st.form_submit_button("Confirm setup", type="primary"):
                    try:
                        result = totp_confirm_setup(st.session_state.user_id, otp)
                        st.session_state.totp_backup_codes_once = result.backup_codes
                        st.session_state.pop("totp_setup_secret", None)
                        st.session_state.pop("totp_setup_uri", None)
                        st.rerun()
                    except TOTPError as exc:
                        st.error(str(exc))
            if st.button("Cancel setup", key="2fa_cancel"):
                st.session_state.pop("totp_setup_secret", None)
                st.session_state.pop("totp_setup_uri", None)
                st.rerun()

        # One-shot display of freshly-generated backup codes.
        if st.session_state.get("totp_backup_codes_once"):
            st.success("✅ 2FA enabled!")
            st.warning(
                "**Save these backup codes now.** Each works once — store "
                "them somewhere safe. You will NOT see them again."
            )
            for code in st.session_state["totp_backup_codes_once"]:
                st.code(code, language=None)
            if st.button("I've saved them — hide"):
                st.session_state.pop("totp_backup_codes_once", None)
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

with st.sidebar.expander("📲 Link Telegram"):
    link = get_link(st.session_state.user_id)
    if link is None:
        st.caption(
            "Generate a binding token, then send `/bind <token>` to the bot. "
            "Once linked, you'll get a Telegram ping when any of your saved "
            "applications gets a new pipeline stage."
        )
        if st.button("Generate binding token"):
            tok = issue_binding_token(st.session_state.user_id)
            st.code(f"/bind {tok}", language=None)
            st.caption("Valid for 15 minutes. Paste into the bot.")
    else:
        username = f"@{link.chat_username}" if link.chat_username else f"chat {link.chat_id}"
        st.success(f"Linked to {username}.")
        notify_key = f"notify_on_stage_pref_{st.session_state.user_id}"
        new_pref = st.toggle(
            "Notify on new stage events",
            value=link.notify_on_stage,
            key=notify_key,
        )
        if new_pref != link.notify_on_stage:
            try:
                set_notify_on_stage(st.session_state.user_id, new_pref)
                st.rerun()
            except TelegramLinkError as exc:
                st.error(str(exc))

        # Inactivity-reminder threshold (0 disables the inactivity ping
        # without losing per-stage notifications).
        threshold_key = f"inactive_days_{st.session_state.user_id}"
        new_threshold = st.number_input(
            "Inactivity reminder threshold (days, 0 = off)",
            min_value=0,
            max_value=180,
            value=link.inactive_reminder_days,
            step=1,
            key=threshold_key,
        )
        if int(new_threshold) != link.inactive_reminder_days:
            try:
                set_inactive_threshold(st.session_state.user_id, int(new_threshold))
                st.rerun()
            except PermissionError as exc:
                st.error(str(exc))
        st.caption(
            "Scheduled job (`python -m worker.reminders`) sends a Telegram "
            "summary of applications that haven't moved within this window."
        )

        if st.button("Disconnect"):
            unlink(st.session_state.user_id)
            st.rerun()

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

    # ----- Background analyses card (auto-refreshing fragment) -------------
    @st.fragment(run_every="5s")
    def _render_background_card():
        """Polls Celery every 5s for the user's pending background analyses.

        Streamlit re-runs only this fragment on the interval — the form below
        stays stable while pending tasks update. When the user opens a
        completed task, we hand it off to ``session_state`` so the main script
        rerun picks it up and renders it via ``render_result``.
        """
        if not async_enabled():
            return  # nothing to show when the queue isn't configured
        backgrounds = refresh_all_pending(st.session_state.user_id)
        if not backgrounds:
            return
        st.subheader("🛰 Background analyses")
        for bg in backgrounds:
            badge = {
                "PENDING": "⏳ Pending",
                "STARTED": "🏃 Running",
                "SUCCESS": "✅ Done",
                "FAILURE": "❌ Failed",
                "REVOKED": "🛑 Cancelled",
            }.get(bg.state, bg.state)
            with st.container(border=True):
                cols = st.columns([4, 1, 1, 1])
                with cols[0]:
                    st.markdown(f"**{bg.title}** — {badge}")
                    if bg.inputs_summary:
                        st.caption(bg.inputs_summary)
                    if bg.state == "FAILURE" and bg.error_message:
                        st.error(bg.error_message)
                    st.caption(
                        f"Submitted {bg.created_at.strftime('%Y-%m-%d %H:%M')} · "
                        f"task `{bg.task_id[:12]}…`"
                    )
                with cols[1]:
                    if bg.state == "SUCCESS":
                        if st.button("Open", key=f"bg_open_{bg.id}"):
                            st.session_state.last_result = bg.result_json
                            extracted = (bg.result_json or {}).get("job_details", {}).get("extracted_details", {})
                            st.session_state.last_inputs = {
                                "company_name": extracted.get("company_name", ""),
                                "job_title": extracted.get("job_title", ""),
                                "location": extracted.get("location", ""),
                                "compensation": extracted.get("compensation", ""),
                            }
                            st.rerun()
                with cols[2]:
                    if bg.state not in ("SUCCESS", "FAILURE", "REVOKED"):
                        st.caption("Auto-refreshing")
                with cols[3]:
                    if st.button("🗑️", key=f"bg_del_{bg.id}"):
                        try:
                            delete_background_analysis(
                                st.session_state.user_id, bg.id,
                            )
                            st.rerun()
                        except BackgroundAnalysisError as exc:
                            st.error(str(exc))

    _render_background_card()

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

        if async_enabled():
            run_background = st.checkbox(
                "Run in background (returns immediately; pick up the result here later)",
                value=False,
                key="run_background_checkbox",
            )
        else:
            run_background = False
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

            # Background path: enqueue, persist the tracking row, and exit
            # without blocking. The fragment above will surface the result
            # whenever Celery says SUCCESS.
            if run_background:
                with st.spinner("Submitting to background worker…"):
                    bg = submit_background_analysis(
                        st.session_state.user_id,
                        posting_text,
                        manual_inputs=job_data,
                        model=selected_model,
                        resume_text=resume_text,
                    )
                if bg is None:
                    st.error(
                        "Background mode requires a configured Celery broker — "
                        "falling back to inline run."
                    )
                else:
                    st.success(
                        f"Submitted as background analysis **#{bg.id}**. "
                        "The card above auto-refreshes; pick the result up "
                        "any time, including from a different session."
                    )
                    st.stop()

            # Stable per-(user, inputs) checkpoint key so retrying the same
            # form short-circuits stages that already completed.
            checkpoint_key = compute_checkpoint_key(
                posting_text, job_data, selected_model, resume_text,
                user_id=st.session_state.user_id,
            )
            ckpt_store = get_checkpoint_store()
            prior = ckpt_store.completed_stages(checkpoint_key)
            if prior:
                friendly = ", ".join(prior)
                with tool_findings:
                    st.info(
                        f"♻ Resuming — stages already completed in this "
                        f"session: **{friendly}**. Only missing stages will "
                        "re-run."
                    )

            with st.spinner("Analyzing job posting..."):
                result = run_analysis(
                    posting_text,
                    job_data,
                    selected_model,
                    progress_callback=update_progress,
                    resume_text=resume_text,
                    checkpoint_key=checkpoint_key,
                )

            if result.get("error"):
                st.error(f"Analysis failed: {result['error']}")
                done = ckpt_store.completed_stages(checkpoint_key)
                if done:
                    st.info(
                        "Earlier stages succeeded and were saved: "
                        + ", ".join(done)
                        + ". Click **🔍 Analyze posting** again to resume "
                        "from where it failed — only the missing stages "
                        "will re-run."
                    )
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
                # Clear the checkpoint — we have a complete result now.
                ckpt_store.clear(checkpoint_key)

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
    # Bulk import lives at the top so a new user can light up analytics by
    # importing their history before they've saved anything in-app.
    with st.expander("📥 Import past applications"):
        st.caption(
            "Bootstrap your analytics by importing applications you've "
            "already sent. Paste a CSV (`company_name,job_title,location,"
            "applied_on,status,verdict,notes`) or any free-form list. "
            "Previews always render before saving — you approve the final list."
        )
        app_csv_tab, app_free_tab = st.tabs(["CSV", "Free-form (LLM)"])
        with app_csv_tab:
            app_csv = st.text_area(
                "CSV text",
                height=150,
                key="app_import_csv",
                placeholder=(
                    "company_name,job_title,location,applied_on,status,verdict,notes\n"
                    "Acme,ML Engineer,Berlin,2026-03-12,interviewing,Recommended,\n"
                ),
            )
            if st.button("Preview from CSV", key="app_csv_preview"):
                try:
                    st.session_state["app_import_previews"] = parse_applications_csv(app_csv)
                except BulkImportError as exc:
                    st.error(str(exc))
        with app_free_tab:
            app_free = st.text_area(
                "Paste a list of applications",
                height=200,
                key="app_import_free",
                placeholder="Anything: a list of past applications with dates, statuses, notes…",
            )
            if st.button("Parse with LLM", key="app_free_preview"):
                with st.spinner("Parsing applications…"):
                    try:
                        st.session_state["app_import_previews"] = parse_applications_freeform(app_free)
                    except BulkImportError as exc:
                        st.error(str(exc))

        app_previews = st.session_state.get("app_import_previews") or []
        if app_previews:
            st.markdown(f"**Preview — {len(app_previews)} application(s) detected**")
            ap_keep = []
            for idx, p in enumerate(app_previews):
                cols = st.columns([1, 6])
                with cols[0]:
                    chosen = st.checkbox(
                        "Include",
                        value=True,
                        key=f"app_keep_{idx}",
                        label_visibility="collapsed",
                    )
                with cols[1]:
                    applied_str = (
                        p["applied_on"].isoformat() if p.get("applied_on") else "—"
                    )
                    st.markdown(
                        f"**{p['job_title']}** @ {p['company_name']} · "
                        f"_{p['status']}_ · applied {applied_str}"
                    )
                    if p.get("location"):
                        st.caption(p["location"])
                    if p.get("notes"):
                        st.caption(p["notes"])
                if chosen:
                    ap_keep.append(p)
            col_s, col_c = st.columns(2)
            if col_s.button(
                f"💾 Import {len(ap_keep)} application(s)",
                type="primary",
                key="app_import_save",
                disabled=(len(ap_keep) == 0),
            ):
                ids = save_applications(st.session_state.user_id, ap_keep)
                st.session_state.pop("app_import_previews", None)
                st.success(
                    f"Imported {len(ids)} application(s). "
                    "Analytics will reflect them on the next refresh."
                )
                st.rerun()
            if col_c.button("Discard preview", key="app_import_clear"):
                st.session_state.pop("app_import_previews", None)
                st.rerun()

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

                    # Snooze inactivity reminders for this application.
                    today = date.today()
                    if rec.snooze_reminders_until and rec.snooze_reminders_until >= today:
                        st.caption(
                            f"🔕 Reminders snoozed until "
                            f"{rec.snooze_reminders_until.isoformat()}"
                        )
                        if st.button("Unsnooze", key=f"unsnooze_{rec.id}"):
                            unsnooze_application(st.session_state.user_id, rec.id)
                            st.rerun()
                    else:
                        snooze_choice = st.selectbox(
                            "🔕 Snooze reminders",
                            ["Don't snooze", "7 days", "14 days", "30 days"],
                            key=f"snooze_sel_{rec.id}",
                            label_visibility="collapsed",
                        )
                        if snooze_choice != "Don't snooze":
                            days = int(snooze_choice.split()[0])
                            if st.button(f"Snooze {days}d", key=f"snooze_btn_{rec.id}"):
                                snooze_application(
                                    st.session_state.user_id, rec.id,
                                    today + timedelta(days=days),
                                )
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

                # ----- Pipeline timeline -----
                timeline_points = per_application_timeline(
                    st.session_state.user_id, rec.id,
                )
                if timeline_points:
                    st.markdown("##### 📅 Pipeline timeline")
                    tl_df = pd.DataFrame(points_to_records(timeline_points))
                    # Sort kinds by funnel order so Y axis reads top→bottom.
                    kind_order = list(
                        tl_df.sort_values("pipeline_index")["kind"].unique()
                    )
                    base = alt.Chart(tl_df).encode(
                        x=alt.X("occurred_on:T", title="Date"),
                        y=alt.Y("kind:N", sort=kind_order, title="Stage"),
                        tooltip=[
                            alt.Tooltip("kind:N", title="Stage"),
                            alt.Tooltip("occurred_on:T", title="Date"),
                            alt.Tooltip("notes:N", title="Notes"),
                        ],
                    )
                    line = base.mark_line(color="#6c757d", strokeWidth=2)
                    dots = base.mark_circle(size=180, stroke="white", strokeWidth=1.5).encode(
                        color=alt.Color(
                            "kind:N", legend=None,
                            scale=alt.Scale(
                                domain=list(STAGE_COLORS.keys()),
                                range=list(STAGE_COLORS.values()),
                            ),
                        ),
                    )
                    st.altair_chart(
                        (line + dots).properties(height=180),
                        use_container_width=True,
                    )

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
                for idx, (qa_label, kind) in enumerate(QUICK_ACTIONS):
                    if qa_cols[idx % 3].button(qa_label, key=f"qa_{rec.id}_{kind}"):
                        try:
                            new_stage = add_stage(
                                st.session_state.user_id, rec.id, kind=kind,
                            )
                            # Best-effort Telegram notification — never raises.
                            notify_stage_added(
                                st.session_state.user_id, rec, new_stage,
                            )
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
                                new_stage = add_stage(
                                    st.session_state.user_id,
                                    rec.id,
                                    kind=stage_kind,
                                    occurred_on=stage_date,
                                    notes=stage_notes or None,
                                    at_pipeline_stage=(
                                        None if at_pipeline == "(none)" else at_pipeline
                                    ),
                                )
                                notify_stage_added(
                                    st.session_state.user_id, rec, new_stage,
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

                # A/B comparison toggle — pick two artifacts of the same kind
                # and view them side by side, optionally as a unified diff.
                if len(arts) >= 2:
                    compare_key = f"compare_mode_{rec.id}"
                    if st.toggle(
                        "🆚 Compare two artifacts",
                        key=compare_key,
                        value=st.session_state.get(compare_key, False),
                    ):
                        kinds_present = {a.kind for a in arts}
                        ab_kind = st.selectbox(
                            "Kind",
                            sorted(kinds_present),
                            format_func=lambda k: "Tailored CV" if k == "tailored_cv" else "Cover letter",
                            key=f"compare_kind_{rec.id}",
                        )
                        same_kind = [a for a in arts if a.kind == ab_kind]
                        if len(same_kind) < 2:
                            st.info(
                                f"Need at least 2 saved {ab_kind!r} artifacts "
                                "for this application to compare. Generate "
                                "another version above first."
                            )
                        else:
                            options = {
                                f"#{a.id} · {a.created_at.strftime('%Y-%m-%d %H:%M')}": a
                                for a in same_kind
                            }
                            labels = list(options.keys())
                            a_label = st.selectbox(
                                "Version A", labels, index=0,
                                key=f"compare_a_{rec.id}",
                            )
                            b_label = st.selectbox(
                                "Version B", labels, index=min(1, len(labels) - 1),
                                key=f"compare_b_{rec.id}",
                            )
                            art_a = options[a_label]
                            art_b = options[b_label]
                            if art_a.id == art_b.id:
                                st.caption("_Pick two different versions to compare._")
                            else:
                                col_a, col_b = st.columns(2)
                                for col, art in ((col_a, art_a), (col_b, art_b)):
                                    with col:
                                        check = ConstraintCheck.from_dict(
                                            (art.meta or {}).get("constraint_check")
                                        )
                                        badge = "✅" if check.is_clean else "⚠️"
                                        st.markdown(
                                            f"**#{art.id}** · "
                                            f"{art.created_at.strftime('%Y-%m-%d %H:%M')} "
                                            f"· {badge}"
                                        )
                                        if art.kind == "cover_letter":
                                            tone = (art.meta or {}).get("tone")
                                            if tone:
                                                st.caption(f"tone: `{tone}`")
                                        st.markdown(art.content)
                                with st.expander("📐 Show diff (A → B)"):
                                    view_key = f"art_diff_view_{rec.id}_{art_a.id}_{art_b.id}"
                                    view = st.radio(
                                        "View",
                                        ["Inline", "Unified"],
                                        horizontal=True,
                                        key=view_key,
                                        label_visibility="collapsed",
                                    )
                                    if art_a.content.strip() == art_b.content.strip():
                                        st.caption(
                                            "_Identical content — no diff to show._"
                                        )
                                    elif view == "Unified":
                                        st.code(
                                            unified_diff(
                                                art_a.content, art_b.content,
                                                before_label=f"#{art_a.id}",
                                                after_label=f"#{art_b.id}",
                                            ),
                                            language="diff",
                                        )
                                    else:
                                        st.markdown(
                                            inline_diff_html(
                                                art_a.content, art_b.content,
                                            ),
                                            unsafe_allow_html=True,
                                        )

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
                                # Soft suggestions — one-click "Add to master
                                # CV" for skill flags, manual-edit hint for
                                # the rest. Wrapped in a sub-expander so it
                                # doesn't dominate the artifact view.
                                suggestions = build_suggestions(check)
                                if suggestions:
                                    with st.expander(
                                        f"💡 Suggested fixes ({len(suggestions)})",
                                        expanded=True,
                                    ):
                                        for sidx, sug in enumerate(suggestions):
                                            sug_col1, sug_col2 = st.columns([4, 1])
                                            with sug_col1:
                                                st.markdown(f"**{sug.title}**")
                                                st.caption(sug.explanation)
                                            with sug_col2:
                                                if sug.auto_appliable:
                                                    btn_key = f"sug_apply_{a.id}_{sidx}"
                                                    if st.button("Add ✓", key=btn_key):
                                                        try:
                                                            apply_skill_addition(
                                                                st.session_state.user_id,
                                                                sug.term,
                                                            )
                                                            # Re-check now that
                                                            # the source has it.
                                                            recheck_artifact(
                                                                st.session_state.user_id,
                                                                a.id,
                                                            )
                                                            st.rerun()
                                                        except (
                                                            MasterCVError,
                                                            TailoringError,
                                                        ) as exc:
                                                            st.error(str(exc))
                                st.caption(
                                    "False positives are possible (the detector is "
                                    "case-folded substring matching, not semantic). "
                                    "If a flagged term is genuinely in your master CV, "
                                    "edit it there and click **Re-check** below."
                                )

                            st.markdown("---")
                            st.markdown(a.content)
                            a_btns = st.columns([1, 1, 2, 2])
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
                                    "⬇️ .md",
                                    data=a.content,
                                    file_name=f"{a.kind}_{rec.company_name}_{a.id}.md",
                                    mime="text/markdown",
                                    key=f"art_dl_md_{a.id}",
                                )
                            with a_btns[3]:
                                try:
                                    pdf_bytes = markdown_to_pdf(
                                        a.content,
                                        title=f"{label} — {rec.company_name}",
                                    )
                                    st.download_button(
                                        "⬇️ .pdf",
                                        data=pdf_bytes,
                                        file_name=f"{a.kind}_{rec.company_name}_{a.id}.pdf",
                                        mime="application/pdf",
                                        key=f"art_dl_pdf_{a.id}",
                                    )
                                except PDFExportError as exc:
                                    st.caption(f"PDF disabled: {exc}")

                # ----- Share links -----
                from services.sharing import (
                    ShareError,
                    create_share,
                    list_shares_for_application,
                    revoke as revoke_share,
                )
                with st.expander("🔗 Share read-only link"):
                    base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
                    existing_shares = list_shares_for_application(
                        st.session_state.user_id, rec.id,
                    )
                    with st.form(f"new_share_{rec.id}"):
                        col_ttl, col_art = st.columns(2)
                        with col_ttl:
                            ttl_choice = st.selectbox(
                                "Expiry",
                                ["7 days", "30 days", "Never"],
                                key=f"share_ttl_{rec.id}",
                            )
                        with col_art:
                            include_artifacts = st.checkbox(
                                "Include tailored artifacts",
                                value=False,
                                key=f"share_arts_{rec.id}",
                            )
                        if st.form_submit_button("Create share link", type="primary"):
                            try:
                                ttl_days = None if ttl_choice == "Never" else int(ttl_choice.split()[0])
                                share = create_share(
                                    st.session_state.user_id, rec.id,
                                    ttl_days=ttl_days,
                                    include_artifacts=include_artifacts,
                                )
                                link = (
                                    f"{base_url}/?share={share.token}"
                                    if base_url else f"?share={share.token}"
                                )
                                st.success("Share link created.")
                                st.code(link, language=None)
                            except ShareError as exc:
                                st.error(str(exc))

                    if existing_shares:
                        st.markdown("**Existing share links**")
                        for s in existing_shares:
                            status = "active"
                            if s.revoked_at:
                                status = "revoked"
                            elif s.expires_at and s.expires_at <= __import__("datetime").datetime.utcnow():
                                status = "expired"
                            col_info, col_act = st.columns([4, 1])
                            with col_info:
                                exp = s.expires_at.strftime("%Y-%m-%d") if s.expires_at else "Never"
                                st.caption(
                                    f"#{s.id} · _{status}_ · expires {exp} · "
                                    f"{s.view_count} view(s)"
                                )
                                if status == "active":
                                    link = (
                                        f"{base_url}/?share={s.token}"
                                        if base_url else f"?share={s.token}"
                                    )
                                    st.code(link, language=None)
                            with col_act:
                                if status == "active":
                                    if st.button("Revoke", key=f"share_revoke_{s.id}"):
                                        revoke_share(st.session_state.user_id, s.id)
                                        st.rerun()

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

        # ----- Cross-application swimlane -----
        swim = cross_application_swimlane(st.session_state.user_id)
        if swim:
            st.subheader("All applications — stage timeline")
            st.caption(
                "Each row is one application; each dot is a stage event. "
                "Useful for spotting velocity differences and where applications "
                "tend to stall."
            )
            swim_df = pd.DataFrame(points_to_records(swim))
            # Order rows by earliest stage so newest activity is at the top.
            label_order = (
                swim_df.groupby("application_label")["occurred_on"]
                .min()
                .sort_values(ascending=False)
                .index.tolist()
            )
            base = alt.Chart(swim_df).encode(
                x=alt.X("occurred_on:T", title="Date"),
                y=alt.Y(
                    "application_label:N",
                    sort=label_order,
                    title=None,
                    axis=alt.Axis(labelLimit=400),
                ),
                tooltip=[
                    alt.Tooltip("application_label:N", title="Application"),
                    alt.Tooltip("kind:N", title="Stage"),
                    alt.Tooltip("occurred_on:T", title="Date"),
                    alt.Tooltip("notes:N", title="Notes"),
                ],
            )
            line_per_app = base.mark_line(color="#9aa0a6", strokeWidth=1).encode(
                detail="application_label:N",
            )
            dots_per_app = base.mark_circle(size=130, stroke="white", strokeWidth=1.2).encode(
                color=alt.Color(
                    "kind:N", title="Stage",
                    scale=alt.Scale(
                        domain=list(STAGE_COLORS.keys()),
                        range=list(STAGE_COLORS.values()),
                    ),
                ),
            )
            row_height = 28
            chart_height = max(180, row_height * len(label_order) + 60)
            st.altair_chart(
                (line_per_app + dots_per_app).properties(height=chart_height),
                use_container_width=True,
            )


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
            parse_col, pdf_col, del_col = st.columns([2, 1, 1])
            with parse_col:
                if st.button("🧠 Parse into structured sections (optional)"):
                    with st.spinner("Asking the model to structure your CV…"):
                        try:
                            structured = parse_master_cv(st.session_state.user_id)
                            st.success("Parsed.")
                            st.json(structured)
                        except MasterCVError as exc:
                            st.error(str(exc))
            with pdf_col:
                try:
                    cv_pdf = markdown_to_pdf(
                        current_cv.raw_text, title="Master CV"
                    )
                    st.download_button(
                        "⬇️ Master CV .pdf",
                        data=cv_pdf,
                        file_name="master_cv.pdf",
                        mime="application/pdf",
                        key="master_cv_pdf",
                    )
                except PDFExportError as exc:
                    st.caption(f"PDF disabled: {exc}")
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

            # ----- Revisions -----
            revisions = list_revisions(st.session_state.user_id)
            if revisions:
                with st.expander(f"🕘 Revision history ({len(revisions)})"):
                    st.caption(
                        "Every content change snapshots the previous version. "
                        "Restoring rewrites the current master CV from the chosen "
                        "revision and itself creates a snapshot you can undo to."
                    )
                    for rev in revisions:
                        rcol1, rcol2 = st.columns([5, 1])
                        with rcol1:
                            reason = f" · _{rev.reason}_" if rev.reason else ""
                            st.markdown(
                                f"**#{rev.id}** — "
                                f"{rev.created_at.strftime('%Y-%m-%d %H:%M')}"
                                f"{reason} — {len(rev.raw_text)} chars"
                            )
                            with st.expander("Preview", expanded=False):
                                st.text(rev.raw_text[:2000] + ("…" if len(rev.raw_text) > 2000 else ""))
                            with st.expander("🔀 Diff against current", expanded=False):
                                view_key = f"rev_diff_view_{rev.id}"
                                view = st.radio(
                                    "View",
                                    ["Inline", "Unified"],
                                    horizontal=True,
                                    key=view_key,
                                    label_visibility="collapsed",
                                )
                                diff_text = diff_revision_against_current(
                                    st.session_state.user_id, rev.id,
                                )
                                if not diff_text.strip():
                                    st.caption(
                                        "_No textual differences from the "
                                        "current master CV._"
                                    )
                                elif view == "Unified":
                                    st.code(diff_text, language="diff")
                                else:
                                    # Re-fetch the two raw bodies for the
                                    # word-level highlighting; we already know
                                    # they differ.
                                    cv_now = get_master_cv(st.session_state.user_id)
                                    st.markdown(
                                        inline_diff_html(
                                            rev.raw_text, cv_now.raw_text,
                                        ),
                                        unsafe_allow_html=True,
                                    )
                        with rcol2:
                            armed_key = f"rev_restore_armed_{rev.id}"
                            if st.session_state.get(armed_key):
                                if st.button("Confirm", key=f"rev_yes_{rev.id}",
                                             type="primary"):
                                    try:
                                        restore_revision(
                                            st.session_state.user_id, rev.id
                                        )
                                        st.session_state.pop(armed_key, None)
                                        st.rerun()
                                    except MasterCVError as exc:
                                        st.error(str(exc))
                                if st.button("Cancel", key=f"rev_no_{rev.id}"):
                                    st.session_state.pop(armed_key, None)
                                    st.rerun()
                            else:
                                if st.button("↩ Restore", key=f"rev_{rev.id}"):
                                    st.session_state[armed_key] = True
                                    st.rerun()
                            if st.button("🗑️", key=f"rev_del_{rev.id}"):
                                try:
                                    delete_revision(
                                        st.session_state.user_id, rev.id
                                    )
                                    st.rerun()
                                except MasterCVError as exc:
                                    st.error(str(exc))
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

        with st.expander("📥 Bulk import projects"):
            st.caption(
                "Paste a CSV (`title,role,tech_stack,summary,highlights,url`) "
                "or any free-form portfolio dump — we'll preview before saving. "
                "Imports are never auto-persisted; you approve the final list."
            )
            import_tab_csv, import_tab_free = st.tabs(["CSV", "Free-form (LLM)"])

            with import_tab_csv:
                csv_text = st.text_area(
                    "CSV text",
                    height=150,
                    key="proj_import_csv",
                    placeholder="title,role,tech_stack,summary,highlights,url\nRecsys,Lead,Python|PyTorch,...,Used by 10M users|1.2k stars,https://...",
                )
                if st.button("Preview from CSV", key="proj_csv_preview"):
                    try:
                        st.session_state["proj_import_previews"] = parse_projects_csv(csv_text)
                    except BulkImportError as exc:
                        st.error(str(exc))

            with import_tab_free:
                free_text = st.text_area(
                    "Paste a portfolio dump",
                    height=200,
                    key="proj_import_free",
                    placeholder="Anything goes — a list of projects, a portfolio paragraph, GitHub README content…",
                )
                if st.button("Parse with LLM", key="proj_free_preview"):
                    with st.spinner("Parsing projects…"):
                        try:
                            st.session_state["proj_import_previews"] = parse_projects_freeform(free_text)
                        except BulkImportError as exc:
                            st.error(str(exc))

            previews = st.session_state.get("proj_import_previews") or []
            if previews:
                st.markdown(f"**Preview — {len(previews)} project(s) detected**")
                keep = []
                for idx, p in enumerate(previews):
                    cols = st.columns([1, 6])
                    with cols[0]:
                        chosen = st.checkbox(
                            "Include",
                            value=True,
                            key=f"proj_keep_{idx}",
                            label_visibility="collapsed",
                        )
                    with cols[1]:
                        st.markdown(
                            f"**{p['title']}**"
                            + (f" — {p['role']}" if p.get("role") else "")
                        )
                        if p.get("tech_stack"):
                            st.caption(f"Tech: {p['tech_stack']}")
                        if p.get("summary"):
                            st.caption(p["summary"])
                        if p.get("highlights"):
                            for h in p["highlights"]:
                                st.write(f"- {h}")
                    if chosen:
                        keep.append(p)
                col_save, col_clear = st.columns(2)
                if col_save.button(
                    f"💾 Save {len(keep)} project(s)",
                    type="primary",
                    key="proj_import_save",
                    disabled=(len(keep) == 0),
                ):
                    saved = save_projects(st.session_state.user_id, keep)
                    st.session_state.pop("proj_import_previews", None)
                    st.success(f"Imported {len(saved)} project(s).")
                    st.rerun()
                if col_clear.button("Discard preview", key="proj_import_clear"):
                    st.session_state.pop("proj_import_previews", None)
                    st.rerun()

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
