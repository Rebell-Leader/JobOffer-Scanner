import streamlit as st

# Page config
st.set_page_config(
    page_title="AI Job Analysis Platform",
    page_icon="💼",
    layout="wide"
)

# Title and description
st.title("AI Job Analysis Platform")
st.write("Analyze job postings with AI-powered insights")

# Main form
with st.form("job_analysis_form"):
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
        with st.spinner("Analyzing job posting..."):
            from agents.orchestrator import run_analysis

            # Prepare input
            job_data = {
                "company_name": company_name,
                "job_title": job_title,
                "location": location,
                "compensation": compensation
            }

            # Run analysis
            result = run_analysis(job_description, job_data)

            if result.get("error"):
                st.error(f"Analysis failed: {result['error']}")
            else:
                st.success("Analysis completed!")

                # Display results in expandable sections
                with st.expander("🎯 Job Analysis", expanded=True):
                    st.write(result["job_details"])

                with st.expander("🏢 Company Research", expanded=True):
                    st.write(result["company_analysis"])

                with st.expander("💰 Compensation Analysis", expanded=True):
                    st.write(result["salary_analysis"])

                st.subheader("📑 Final Recommendation")
                st.markdown(result["final_report"])
    else:
        st.warning("Please fill in all required fields marked with *")

# Sidebar with info
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