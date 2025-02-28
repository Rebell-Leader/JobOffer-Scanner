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

    col1, col2 = st.columns(2)

    with col1:
        st.write("📋 Features Extracted:")
        if extracted_details:
            st.success("✅ Job details parsed successfully")
            st.write("- Company Name")
            st.write("- Location")
            st.write("- Required Skills")
            st.write("- Tasks")
            st.write("- Compensation")
        else:
            st.warning("⏳ Extracting job details...")

    with col2:
        st.write("🔍 Tools Analysis:")
        if result.get("company_analysis"):
            st.success("✅ Company research completed")
            st.write("- Layoffs Analysis")
            st.write("- Salary Level Analysis")
            st.write("- Glassdoor Reviews")
            st.write("- Cost of Living")
        else:
            st.warning("⏳ Analyzing company data...")

def get_job_form():
    """Display the job posting form."""
    st.write("Analyze job postings with AI-powered insights")

    job_text = st.text_area(
        "Paste the job posting here",
        height=200,
        placeholder="Paste the complete job posting text here...",
        key="job_posting_input"
    )

    analyze_clicked = st.button("Analyze Job", type="primary")
    return job_text, analyze_clicked

def get_manual_inputs(auto_extracted=None):
    """Show form for manual job details input."""
    st.subheader("📝 Job Details")
    st.info("Please verify or update the information below")

    defaults = auto_extracted or {}

    with st.form("job_details_form"):
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

        submitted = st.form_submit_button("Update Analysis", type="primary")

        if submitted:
            return {
                "company_name": company_name,
                "job_title": job_title,
                "location": location,
                "experience_level": experience,
                "compensation": compensation,
                "job_type": job_type,
                "required_skills": [skill.strip() for skill in skills.split("\n") if skill.strip()]
            }
        return None

def main():
    # Header
    st.title("🤖 AI Job Analysis Platform")

    # Initialize session state
    if "analysis_started" not in st.session_state:
        st.session_state.analysis_started = False
    if "job_text" not in st.session_state:
        st.session_state.job_text = ""
    if "analysis_result" not in st.session_state:
        st.session_state.analysis_result = None

    # Main content
    with st.container():
        job_text, analyze_clicked = get_job_form()

        if analyze_clicked and job_text:
            st.session_state.job_text = job_text
            st.session_state.analysis_started = True

            with st.spinner("Analyzing job posting..."):
                result = run_analysis(job_text)
                st.session_state.analysis_result = result

                if result.get("error"):
                    st.warning("Automatic extraction needs verification. Please review the details below.")

                # Show progress tracking
                display_analysis_progress(result)

                # Show manual input form
                auto_extracted = result.get("job_details", {}).get("extracted_details", {})
                manual_inputs = get_manual_inputs(auto_extracted)

                if manual_inputs:
                    updated_result = run_analysis(job_text, manual_inputs)
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

        elif st.session_state.analysis_started and st.session_state.analysis_result:
            # Continue showing previous analysis results
            display_analysis_progress(st.session_state.analysis_result)
            auto_extracted = st.session_state.analysis_result.get("job_details", {}).get("extracted_details", {})
            get_manual_inputs(auto_extracted)

    # Sidebar
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
    main()