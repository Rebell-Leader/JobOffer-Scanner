import streamlit as st

from agents.orchestrator import run_analysis
from tools.resume_tools import extract_resume_text
from tools.url_ingest import fetch_job_posting, is_url
from utils.config import check_environment_setup, print_environment_status

# Page config
st.set_page_config(page_title="AI Job Analysis Platform", page_icon="💼", layout="wide")

# Print environment status to console for debugging
print_environment_status()

st.title("AI Job Analysis Platform")
st.write("Analyze job postings with AI-powered insights")

env_status = check_environment_setup()
if env_status["demo_mode"]:
    st.info(
        "🔄 **Demo Mode**: No LLM provider key is configured, so results use "
        "**sample data** and do not reflect the posting you submit. Set "
        "`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `FEATHERLESS_API_KEY` "
        "(see `.env.example`) to enable real analysis."
    )
else:
    st.success(
        f"🚀 **Production Mode**: Live analysis via **{env_status['llm_provider']}**."
    )


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------

with st.form("job_analysis_form"):
    model_choice = st.radio(
        "Select analysis depth:",
        ["Fast", "Detailed"],
        index=0,
        help="Fast = quicker, cheaper model. Detailed = deeper reasoning model.",
    )

    col1, col2 = st.columns(2)
    with col1:
        company_name = st.text_input("Company Name*", help="Enter the company name")
        job_title = st.text_input("Job Title*", help="Enter the position title")
    with col2:
        location = st.text_input("Location*", help="Enter job location (city, country)")
        compensation = st.text_input("Compensation", help="Salary/compensation details (optional)")

    job_url = st.text_input(
        "Job posting URL (optional)",
        help=(
            "If provided, we'll fetch the page and use its text. JS-heavy sites "
            "(LinkedIn / Indeed / Glassdoor) often won't work — paste below instead."
        ),
    )

    job_description = st.text_area(
        "Full Job Description*",
        height=260,
        help="Paste the complete job posting text here (used if no URL is provided).",
    )

    resume_file = st.file_uploader(
        "Resume (optional — PDF, DOCX, or TXT)",
        type=["pdf", "docx", "txt", "md"],
        help="Upload a resume to get an ATS keyword-match score and gap analysis.",
    )

    analyze_submitted = st.form_submit_button("Analyze Job", type="primary")


# ---------------------------------------------------------------------------
# Submission handling
# ---------------------------------------------------------------------------

if analyze_submitted:
    # Resolve job posting text from URL or paste.
    posting_text = job_description or ""
    if job_url and is_url(job_url):
        with st.spinner(f"Fetching {job_url} ..."):
            try:
                posting_text = fetch_job_posting(job_url)
                st.success(f"Fetched {len(posting_text)} characters from URL.")
            except ValueError as exc:
                st.error(str(exc))
                posting_text = job_description or ""

    # Parse resume up-front so errors surface before the LLM pipeline starts.
    resume_text = ""
    if resume_file is not None:
        try:
            resume_text = extract_resume_text(resume_file.getvalue(), resume_file.name)
            if not resume_text.strip():
                st.warning("Resume parsed but contained no extractable text — skipping ATS analysis.")
        except ValueError as exc:
            st.error(f"Resume parsing failed: {exc}")

    if posting_text and company_name and job_title and location:
        progress_container = st.container()
        with progress_container:
            st.subheader("Analysis Progress")
            progress_bar = st.progress(0)
            status_text = st.empty()
            status_text.text("Starting analysis...")
            tool_findings = st.container()

        with st.spinner("Analyzing job posting..."):
            job_data = {
                "company_name": company_name,
                "job_title": job_title,
                "location": location,
                "compensation": compensation,
            }
            selected_model = "detailed" if "Detailed" in model_choice else "fast"
            st.session_state.selected_model = selected_model

            status_text.text("Analyzing job requirements...")
            progress_bar.progress(25)

            def update_progress(stage, progress, stage_info=None):
                if stage == "job":
                    status_text.text("Extracting job requirements...")
                    progress_bar.progress(25)
                elif stage == "company":
                    status_text.text("Researching company information...")
                    progress_bar.progress(50)
                    if stage_info:
                        with tool_findings:
                            st.info(f"**Company Research:** {stage_info}")
                elif stage == "salary":
                    status_text.text("Analyzing compensation and cost of living...")
                    progress_bar.progress(75)
                    if stage_info:
                        with tool_findings:
                            st.info(f"**Salary Analysis:** {stage_info}")
                elif stage == "resume":
                    status_text.text("Running ATS / resume gap analysis...")
                    progress_bar.progress(85)
                    if stage_info:
                        with tool_findings:
                            st.info(f"**Resume:** {stage_info}")
                elif stage == "report":
                    status_text.text("Generating final recommendation...")
                    progress_bar.progress(100)

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

                # --- Verdict badge -----------------------------------------------------
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
                    if light == "green":
                        st.success(badge_text)
                    elif light == "red":
                        st.error(badge_text)
                    else:
                        st.warning(badge_text)
                    reasons = verdict.get("reasons") or []
                    if reasons:
                        for r in reasons:
                            st.write(f"- {r}")

                # --- Sections ----------------------------------------------------------
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
    else:
        st.warning("Please provide a posting URL or paste the description, plus the required text fields (*).")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

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
        f"LLM provider: `{env_status['llm_provider']}`. External data sources "
        "(news, salary benchmarks, COL) are still being integrated — figures "
        "from those sections are model estimates and labelled as such."
    )
