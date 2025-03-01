from utils.llm import get_completion
from typing import Dict

def generate(state: Dict) -> Dict:
    if state.get("error"):
        return state

    model = state.get("model", "deepseek-ai/DeepSeek-R1")

    try:
        # Get job details from state
        job_details = state.get("job_details", {})
        extracted_details = job_details.get("extracted_details", {})
        company_name = extracted_details.get("company_name", "the company")
        job_title = extracted_details.get("job_title", "this position")

        # Format job details for the prompt
        job_details_str = "\n".join([
            f"- **Company:** {extracted_details.get('company_name', 'Not specified')}",
            f"- **Position:** {extracted_details.get('job_title', 'Not specified')}",
            f"- **Location:** {extracted_details.get('location', 'Not specified')}",
            f"- **Experience Required:** {extracted_details.get('experience_level', 'Not specified')}",
            f"- **Job Type:** {extracted_details.get('job_type', 'Not specified')}",
            f"- **Compensation:** {extracted_details.get('compensation', 'Not specified')}"
        ])

        prompt = f"""
        Generate a comprehensive job analysis report based on the following data.
        Format the report with clear markdown headings (## and ###) and bullet points.

        ## Job Details:
        {job_details_str}

        ## Requirements Analysis:
        {job_details.get('requirements_analysis', {})}

        ## Company Analysis:
        {state.get('company_analysis', {})}

        ## Salary Analysis:
        {state.get('salary_analysis', {})}

        Your report should include these sections:
        1. Executive Summary - Brief overview of the position and key findings
        2. Company Profile - Information about the company stability and culture
        3. Job Requirements Analysis - Analysis of the required skills and experience
        4. Compensation Analysis - Analysis of the salary and benefits
        5. Final Recommendation - Clear recommendation (Highly Recommended/Recommended/Consider with Caution/Not Recommended)

        Format the recommendation section with a clear heading and include reasons for the recommendation.
        DO NOT include any thinking tokens like <think> or similar in your output.
        Use proper markdown formatting throughout.
        """

        report = get_completion(prompt, model)

        # Ensure the report has proper markdown formatting
        if not report.startswith("#"):
            report = f"# Job Analysis Report: {job_title} at {company_name}\n\n{report}"

        state["final_report"] = report
    except Exception as e:
        state["error"] = f"Report generation failed: {str(e)}"

    return state