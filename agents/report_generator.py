from utils.llm import get_completion
from typing import Dict
import json

def generate(state: Dict) -> Dict:
    if state.get("error"):
        return state

    # Get model and progress callback from state
    model = state.get("model", "deepseek-ai/DeepSeek-R1-0528")
    progress_callback = state.get("progress_callback")

    # Call progress callback if available
    if progress_callback:
        progress_callback("report", 90)

    try:
        # Get job details from state
        job_details = state.get("job_details", {})
        extracted_details = job_details.get("extracted_details", {})
        requirements_analysis = job_details.get("requirements_analysis", {})
        company_name = extracted_details.get("company_name", "the company")
        job_title = extracted_details.get("job_title", "this position")

        print(f"Report generation - extracted_details: {extracted_details}")
        print(f"Report generation - requirements_analysis: {requirements_analysis}")
        print(f"Report generation - company_analysis: {state.get('company_analysis', {})}")
        print(f"Report generation - salary_analysis: {state.get('salary_analysis', {})}")

        # Format job details for the prompt
        job_details_str = "\n".join([
            f"- **Company:** {extracted_details.get('company_name', 'Not specified')}",
            f"- **Position:** {extracted_details.get('job_title', 'Not specified')}",
            f"- **Location:** {extracted_details.get('location', 'Not specified')}",
            f"- **Experience Required:** {extracted_details.get('experience_level', 'Not specified')}",
            f"- **Job Type:** {extracted_details.get('job_type', 'Not specified')}",
            f"- **Compensation:** {extracted_details.get('compensation', 'Not specified')}"
        ])

        # Format requirements analysis
        if isinstance(requirements_analysis, dict):
            req_analysis_str = json.dumps(requirements_analysis, indent=2)
        else:
            req_analysis_str = str(requirements_analysis)

        # Format company analysis
        company_analysis = state.get('company_analysis', {})
        if isinstance(company_analysis, dict):
            company_analysis_str = json.dumps(company_analysis, indent=2)
        else:
            company_analysis_str = str(company_analysis)

        # Format salary analysis
        salary_analysis = state.get('salary_analysis', {})
        if isinstance(salary_analysis, dict):
            salary_analysis_str = json.dumps(salary_analysis, indent=2)
        else:
            salary_analysis_str = str(salary_analysis)

        prompt = f"""
        Generate a comprehensive job analysis report based on the following data.
        Format the report with clear markdown headings (## and ###) and bullet points.

        ## Job Details:
        {job_details_str}

        ## Requirements Analysis:
        {req_analysis_str}

        ## Company Analysis:
        {company_analysis_str}

        ## Salary Analysis:
        {salary_analysis_str}

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

        print(f"Report prompt length: {len(prompt)}")
        report = get_completion(prompt, model)
        print(f"Report generation - report length: {len(report)}")

        # Ensure the report has proper markdown formatting
        if not report.startswith("#"):
            report = f"# Job Analysis Report: {job_title} at {company_name}\n\n{report}"

        # Clean up report - remove any thinking tokens or markdown code block markers
        report = report.replace("<think>", "").replace("</think>", "")

        if "```markdown" in report:
            report = report.replace("```markdown", "").replace("```", "")

        state["final_report"] = report

        # Call progress callback with completion
        if progress_callback:
            progress_callback("report", 100, "Final report generated successfully")

    except Exception as e:
        print(f"Report generation error: {str(e)}")
        state["error"] = f"Report generation failed: {str(e)}"

        # Provide a basic report as fallback
        state["final_report"] = f"""
        # Job Analysis Report: {job_title} at {company_name}

        ## Executive Summary

        We attempted to analyze this job position but encountered some technical issues during processing.

        ## Basic Information

        - **Company:** {extracted_details.get('company_name', 'Not specified')}
        - **Position:** {extracted_details.get('job_title', 'Not specified')}
        - **Location:** {extracted_details.get('location', 'Not specified')}

        Please try again or contact support if this issue persists.
        """

    return state