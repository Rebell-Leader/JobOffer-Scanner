import streamlit as st
from agents.orchestrator import run_analysis
from utils.config import check_environment_setup, print_environment_status

# Page config
st.set_page_config(
    page_title="AI Job Analysis Platform",
    page_icon="💼",
    layout="wide"
)

# Print environment status to console for debugging
print_environment_status()

# Title and description
st.title("AI Job Analysis Platform")
st.write("Analyze job postings with AI-powered insights")

# Show demo mode status
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

# Main form
with st.form("job_analysis_form"):
    # Model selection (logical tier — resolved to a concrete model per provider)
    model_choice = st.radio(
        "Select analysis depth:",
        ["Fast", "Detailed"],
        index=0,  # Default to the faster tier
        help="Fast = quicker, cheaper model. Detailed = deeper reasoning model.",
    )

    # Basic job details
    col1, col2 = st.columns(2)

    with col1:
        company_name = st.text_input(
            "Company Name*",
            help="Enter the company name"
        )

        job_title = st.text_input(
            "Job Title*",
            help="Enter the position title"
        )

    with col2:
        location = st.text_input(
            "Location*",
            help="Enter job location (city, country)"
        )

        compensation = st.text_input(
            "Compensation",
            help="Enter salary/compensation details"
        )

    # Full job description
    job_description = st.text_area(
        "Full Job Description*",
        height=300,
        help="Paste the complete job posting text here"
    )

    # Submit button
    analyze_submitted = st.form_submit_button("Analyze Job", type="primary")

if analyze_submitted:
    if job_description and company_name and job_title and location:
        # Create a progress container
        progress_container = st.container()

        with progress_container:
            st.subheader("Analysis Progress")
            progress_bar = st.progress(0)
            status_text = st.empty()

            # Set initial status
            status_text.text("Starting analysis...")

            # Initialize containers for tool findings
            tool_findings = st.container()

        with st.spinner("Analyzing job posting..."):
            # Prepare input
            job_data = {
                "company_name": company_name,
                "job_title": job_title,
                "location": location,
                "compensation": compensation
            }

            # Pass a logical tier; utils.llm resolves it to the active provider's model.
            selected_model = "detailed" if "Detailed" in model_choice else "fast"
            st.session_state.selected_model = selected_model

            # Update progress - Job analysis stage
            status_text.text("Analyzing job requirements...")
            progress_bar.progress(25)

            # Run analysis with progress callbacks
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
                elif stage == "report":
                    status_text.text("Generating final recommendation...")
                    progress_bar.progress(100)

            # Run analysis
            result = run_analysis(job_description, job_data, selected_model, progress_callback=update_progress)

            if result.get("error"):
                st.error(f"Analysis failed: {result['error']}")
            else:
                # Final progress update
                status_text.text("Analysis completed!")
                progress_bar.progress(100)

                st.success("Analysis completed!")

                # Display results in expandable sections
                with st.expander("🎯 Job Analysis", expanded=True):
                    st.write(result["job_details"])

                    # Display skills found
                    requirements = result["job_details"].get("requirements_analysis", {})
                    if requirements:
                        st.subheader("Key Skills Required")
                        tech_skills = requirements.get("technical_skills", [])
                        if tech_skills:
                            st.write("Technical Skills:")
                            for skill in tech_skills:
                                st.write(f"- {skill}")

                with st.expander("🏢 Company Research", expanded=True):
                    st.write(result["company_analysis"])

                with st.expander("💰 Compensation Analysis", expanded=True):
                    st.write(result["salary_analysis"])

                st.subheader("📑 Final Recommendation")
                st.markdown(result["final_report"])
    else:
        st.warning("Please fill in all required fields marked with *")

# Sidebar
st.sidebar.title("About")
st.sidebar.info(
    "AI-assisted analysis of job postings: requirements, company signals, "
    "compensation and cost-of-living context, plus a final recommendation."
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