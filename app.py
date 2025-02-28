import streamlit as st
from agents.orchestrator import run_analysis

st.set_page_config(
    page_title="AI Job Analysis Platform",
    page_icon="💼",
    layout="wide"
)

def display_analysis_progress(result: dict):
    """Display the analysis pipeline progress with status indicators."""
    st.subheader("🔄 Analysis Progress")

    # Job Details Extraction
    job_details = result.get("job_details", {})
    extracted_details = job_details.get("extracted_details", {})
    st.write("1. Job Details Extraction", "✅" if extracted_details else "⏳")

    # Company Research
    company_analysis = result.get("company_analysis", {})
    if company_analysis:
        st.write("2. Company Research ✅")
        with st.expander("Research Sources"):
            st.write("- 🔍 Web Search Analysis")
            st.write("- 📰 Yahoo Finance News")
            st.write("- 📊 Stock Performance")
            st.write("- 👥 Employee Reviews")
            st.write("- 🚨 Layoffs.fyi Data")
    else:
        st.write("2. Company Research ⏳")

    # Compensation Analysis
    salary_analysis = result.get("salary_analysis", {})
    if salary_analysis:
        st.write("3. Compensation Analysis ✅")
        with st.expander("Analysis Sources"):
            st.write("- 💰 Comprehensive.io Data")
            st.write("- 🏘️ Numbeo Cost of Living")
            st.write("- 📊 Market Rate Analysis")
    else:
        st.write("3. Compensation Analysis ⏳")

    # Final Report
    st.write("4. Final Report", "✅" if result.get("final_report") else "⏳")


def get_manual_inputs(auto_extracted=None):
    """Get manual inputs for job details."""
    st.subheader("📝 Job Details")
    st.info("Please verify or update the job information below")

    # Initialize with auto-extracted values if available
    defaults = auto_extracted or {}

    col1, col2 = st.columns(2)

    with col1:
        company_name = st.text_input(
            "Company Name*",
            value=defaults.get("company_name", ""),
            help="Enter the company name"
        )

        job_title = st.text_input(
            "Job Title*",
            value=defaults.get("job_title", ""),
            help="Enter the position title"
        )

        location = st.text_input(
            "Location*",
            value=defaults.get("location", ""),
            help="Enter job location (city, country)"
        )

    with col2:
        experience = st.text_input(
            "Required Experience",
            value=defaults.get("experience_level", ""),
            help="e.g., '3+ years', 'Senior level'"
        )

        compensation = st.text_input(
            "Compensation",
            value=defaults.get("compensation", ""),
            help="Salary/compensation details"
        )

        job_type = st.selectbox(
            "Job Type*",
            options=["Full-time", "Part-time", "Contract", "Internship", "Other"],
            index=0,
            help="Select employment type"
        )

    skills = st.text_area(
        "Required Skills*",
        value="\n".join(defaults.get("required_skills", [])),
        help="Enter skills, one per line",
        height=100
    )

    # Return structured data
    return {
        "company_name": company_name,
        "job_title": job_title,
        "location": location,
        "experience_level": experience,
        "compensation": compensation,
        "job_type": job_type,
        "required_skills": [skill.strip() for skill in skills.split("\n") if skill.strip()]
    }

def main():
    st.title("🤖 AI Job Analysis Platform")
    st.write("Analyze job postings with AI-powered insights")

    with st.container():
        # Job Posting Input
        job_text = st.text_area(
            "Paste the job posting here",
            height=200,
            placeholder="Paste the complete job posting text here..."
        )

        analyze_button = st.button("Analyze Job", type="primary")

        if analyze_button and job_text:
            st.session_state.job_text = job_text
            st.session_state.analysis_started = True

        if st.session_state.get("analysis_started"):
            with st.spinner("Processing job posting..."):
                # First pass - automatic extraction
                result = run_analysis(st.session_state.job_text)

                # Display progress
                display_analysis_progress(result)

                # Show form for manual input/verification
                auto_extracted = result.get("job_details", {}).get("extracted_details", {})
                manual_inputs = get_manual_inputs(auto_extracted)

                if st.button("Update Analysis", type="primary"):
                    # Rerun analysis with manual inputs
                    updated_result = run_analysis(st.session_state.job_text, manual_inputs)
                    display_analysis_progress(updated_result)

                    if not updated_result.get("error"):
                        st.success("Analysis completed successfully!")

                        with st.expander("🎯 Job Details", expanded=True):
                            st.write(updated_result["job_details"]["extracted_details"])
                            st.write(updated_result["job_details"]["requirements_analysis"])

                        with st.expander("🏢 Company Research", expanded=True):
                            st.write(updated_result["company_analysis"])

                        with st.expander("💰 Compensation Analysis", expanded=True):
                            st.write(updated_result["salary_analysis"])

                        st.subheader("📑 Final Recommendation")
                        st.markdown(updated_result["final_report"])
                    else:
                        st.error(f"Analysis failed: {updated_result['error']}")

    # Sidebar information
    st.sidebar.title("About")
    st.sidebar.info(
        "This AI-powered platform helps you analyze job postings, "
        "evaluate company stability, and make informed career decisions. "
        "We combine data from multiple sources including:\n"
        "- Company news and financials\n"
        "- Market salary data\n"
        "- Cost of living analysis\n"
        "- Industry layoff trends"
    )

if __name__ == "__main__":
    # Initialize session state
    if "analysis_started" not in st.session_state:
        st.session_state.analysis_started = False
    if "job_text" not in st.session_state:
        st.session_state.job_text = ""

    main()