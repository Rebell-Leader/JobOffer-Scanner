import streamlit as st
from agents.orchestrator import run_analysis

st.set_page_config(
    page_title="AI Job Analyzer",
    page_icon="💼",
    layout="wide"
)

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
                            # Display Job Details
                            st.subheader("🎯 Job Details")
                            with st.expander("View Details", expanded=True):
                                st.write(result["job_details"]["extracted_details"])
                                st.write(result["job_details"]["requirements_analysis"])
                            
                            # Display Company Analysis
                            st.subheader("🏢 Company Analysis")
                            with st.expander("View Analysis", expanded=True):
                                st.write(result["company_analysis"]["stability_analysis"])
                                st.write(result["company_analysis"]["company_reviews"])
                            
                            # Display Salary Analysis
                            st.subheader("💰 Salary Analysis")
                            with st.expander("View Analysis", expanded=True):
                                st.write(result["salary_analysis"]["estimated_range"])
                            
                            # Display Final Report
                            st.subheader("📊 Final Report")
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
