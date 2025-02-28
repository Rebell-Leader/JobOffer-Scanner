import streamlit as st
from agents.orchestrator import run_analysis

st.set_page_config(
    page_title="AI Job Analyzer",
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

    # Company Analysis
    company_analysis = result.get("company_analysis", {})
    company_success = bool(company_analysis)
    st.write("2. Company Analysis", "✅" if company_success else "⏳")
    if company_success:
        with st.expander("Analysis Components"):
            st.write("- Stability Analysis ✅")
            st.write("- Company Reviews ✅")

    # Salary Analysis
    salary_analysis = result.get("salary_analysis", {})
    salary_success = bool(salary_analysis)
    st.write("3. Salary Analysis", "✅" if salary_success else "⏳")
    if salary_success:
        with st.expander("Analysis Components"):
            st.write("- Market Rate Analysis ✅")
            st.write("- Cost of Living Adjustment ✅")

    # Final Report
    report_success = bool(result.get("final_report"))
    st.write("4. Final Report Generation", "✅" if report_success else "⏳")

def main():
    st.title("🤖 AI Job Analysis Platform")
    st.write("Analyze job postings with AI-powered insights")

    with st.container():
        st.subheader("📝 Job Posting Analysis")
        job_posting = st.text_area(
            "Paste the job posting here",
            height=200,
            placeholder="Paste the complete job posting text here..."
        )

        if st.button("Analyze Job", type="primary"):
            if job_posting:
                with st.spinner("Analyzing job posting..."):
                    try:
                        result = run_analysis(job_posting)

                        if result.get("error"):
                            st.error(f"Analysis failed: {result['error']}")
                        else:
                            # Display Progress Tracking
                            display_analysis_progress(result)

                            # Display Detailed Results
                            st.subheader("📊 Analysis Results")

                            # Job Details
                            with st.expander("🎯 Job Details", expanded=True):
                                st.write(result["job_details"]["extracted_details"])
                                st.write(result["job_details"]["requirements_analysis"])

                            # Company Analysis
                            with st.expander("🏢 Company Analysis", expanded=True):
                                st.write(result["company_analysis"]["stability_analysis"])
                                st.write(result["company_analysis"]["company_reviews"])

                            # Salary Analysis
                            with st.expander("💰 Salary Analysis", expanded=True):
                                st.write(result["salary_analysis"]["estimated_range"])

                            # Final Report
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