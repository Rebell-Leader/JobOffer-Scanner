import streamlit as st
from agents.orchestrator import run_analysis

# Page config
st.set_page_config(
    page_title="AI Job Analysis Platform",
    page_icon="🤖",
    layout="wide"
)

# Logo and title
col1, col2 = st.columns([1, 4])
with col1:
    st.image("🤖", width=50)
with col2:
    st.title("AI Job Analysis Platform")

st.write("Analyze job postings with AI-powered insights")

# Job posting input
job_text = st.text_area(
    "Paste the job posting here",
    height=200,
    placeholder="Paste the complete job posting text here..."
)

# Analysis button
if st.button("Analyze Job", type="primary"):
    if job_text:
        with st.spinner("Analyzing job posting..."):
            # Run initial analysis
            result = run_analysis(job_text)

            # Display form for verification/manual input
            st.subheader("📝 Job Details")
            st.info("Please verify or update the information below")

            # Get extracted details
            extracted = result.get("job_details", {}).get("extracted_details", {})

            # Manual input form
            col1, col2 = st.columns(2)

            with col1:
                company_name = st.text_input(
                    "Company Name*",
                    value=extracted.get("company_name", ""),
                    help="Enter the company name"
                )

                job_title = st.text_input(
                    "Job Title*",
                    value=extracted.get("job_title", ""),
                    help="Enter the position title"
                )

                location = st.text_input(
                    "Location*",
                    value=extracted.get("location", ""),
                    help="Enter job location (city, country)"
                )

            with col2:
                experience = st.text_input(
                    "Required Experience",
                    value=extracted.get("experience_level", ""),
                    help="e.g., '3+ years', 'Senior level'"
                )

                compensation = st.text_input(
                    "Compensation",
                    value=extracted.get("compensation", ""),
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
                value="\n".join(extracted.get("required_skills", [])),
                help="Enter skills, one per line",
                height=100
            )

            if st.button("Update Analysis", type="primary"):
                manual_inputs = {
                    "company_name": company_name,
                    "job_title": job_title,
                    "location": location,
                    "experience_level": experience,
                    "compensation": compensation,
                    "job_type": job_type,
                    "required_skills": [s.strip() for s in skills.split("\n") if s.strip()]
                }

                updated_result = run_analysis(job_text, manual_inputs)

                # Display analysis results
                st.success("Analysis completed!")

                with st.expander("🎯 Job Details", expanded=True):
                    st.write(updated_result["job_details"])

                with st.expander("🏢 Company Research", expanded=True):
                    st.write(updated_result["company_analysis"])

                with st.expander("💰 Compensation Analysis", expanded=True):
                    st.write(updated_result["salary_analysis"])

                st.subheader("📑 Final Recommendation")
                st.markdown(updated_result["final_report"])
    else:
        st.warning("Please paste a job posting to analyze")

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