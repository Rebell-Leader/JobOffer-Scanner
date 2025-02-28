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
    job_details_success = bool(job_details.get("extracted_details"))
    st.write("1. Job Details Extraction", "✅" if job_details_success else "⏳")
    if job_details_success:
        with st.expander("Extracted Features"):
            details = job_details.get("extracted_details", {})
            st.write("- Company:", details.get("company_name", "Not found"))
            st.write("- Location:", details.get("location", "Not found"))
            st.write("- Required Skills:", ", ".join(details.get("required_skills", [])))
            st.write("- Compensation:", details.get("compensation", "Not specified"))

    # Company Research
    company_analysis = result.get("company_analysis", {})
    company_success = bool(company_analysis)
    st.write("2. Company Research", "✅" if company_success else "⏳")
    if company_success:
        with st.expander("Research Components"):
            st.write("- Company News Analysis ✅")
            st.write("- Layoffs History ✅")
            st.write("- Market Position ✅")
            st.write("- Employee Reviews ✅")

    # Salary & Benefits
    salary_analysis = result.get("salary_analysis", {})
    salary_success = bool(salary_analysis)
    st.write("3. Compensation Analysis", "✅" if salary_success else "⏳")
    if salary_success:
        with st.expander("Analysis Components"):
            st.write("- Market Rate Analysis ✅")
            st.write("- Cost of Living Analysis ✅")
            st.write("- Benefits Evaluation ✅")

    # Final Report
    report_success = bool(result.get("final_report"))
    st.write("4. Final Report Generation", "✅" if report_success else "⏳")

def get_manual_inputs():
    """Get manual inputs for job details when automatic extraction fails."""
    st.subheader("📝 Job Details")
    st.info("Please verify or update the extracted information below.")

    col1, col2 = st.columns(2)

    with col1:
        company_name = st.text_input(
            "Company Name*",
            key="company_name",
            help="Enter the company name as shown in the job posting"
        )

        job_title = st.text_input(
            "Job Title*",
            key="job_title",
            help="Enter the main job title/role"
        )

        location = st.text_input(
            "Location*",
            key="location",
            help="Enter the job location (city, country)"
        )

    with col2:
        experience_level = st.text_input(
            "Required Experience",
            key="experience",
            help="Enter the required years of experience"
        )

        compensation = st.text_input(
            "Compensation",
            key="compensation",
            help="Enter the offered salary/compensation"
        )

        job_type = st.selectbox(
            "Job Type",
            ["Full-time", "Part-time", "Contract", "Other"],
            key="job_type"
        )

    required_skills = st.text_area(
        "Required Skills",
        key="skills",
        help="Enter required skills, one per line"
    )

    return {
        "company_name": company_name,
        "job_title": job_title,
        "location": location,
        "experience_level": experience_level,
        "compensation": compensation,
        "job_type": job_type,
        "required_skills": [skill.strip() for skill in required_skills.split("\n") if skill.strip()],
    }

def main():
    st.title("🤖 AI Job Analysis Platform")
    st.write("Analyze job postings with AI-powered insights")

    with st.container():
        job_posting = st.text_area(
            "Paste the job posting here",
            height=200,
            placeholder="Paste the complete job posting text here..."
        )

        if st.button("Analyze Job", type="primary"):
            if job_posting:
                with st.spinner("Analyzing job posting..."):
                    try:
                        # First attempt automatic extraction
                        result = run_analysis(job_posting)

                        if result.get("error"):
                            st.warning("Automatic extraction needs verification. Please review the details below.")

                        # Display progress and initial results
                        display_analysis_progress(result)

                        # Get manual inputs/corrections
                        manual_inputs = get_manual_inputs()

                        # If manual inputs changed, update and rerun analysis
                        if st.button("Update Analysis"):
                            result = run_analysis(job_posting, manual_inputs)
                            display_analysis_progress(result)

                        # Display detailed results
                        if not result.get("error"):
                            st.subheader("📊 Detailed Analysis")

                            with st.expander("🎯 Job Details", expanded=True):
                                st.write(result["job_details"]["extracted_details"])
                                st.write(result["job_details"]["requirements_analysis"])

                            with st.expander("🏢 Company Research", expanded=True):
                                st.write(result["company_analysis"]["stability_analysis"])
                                st.write(result["company_analysis"]["company_reviews"])

                            with st.expander("💰 Compensation Analysis", expanded=True):
                                st.write(result["salary_analysis"]["estimated_range"])

                            st.subheader("📑 Final Report")
                            st.markdown(result["final_report"])

                    except Exception as e:
                        st.error(f"An error occurred: {str(e)}")
            else:
                st.warning("Please paste a job posting to analyze")

    st.sidebar.title("About")
    st.sidebar.info(
        "This AI-powered platform helps you analyze job postings, "
        "evaluate company stability, and make informed career decisions."
    )

if __name__ == "__main__":
    main()