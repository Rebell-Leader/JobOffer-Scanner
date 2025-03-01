from tools.job_tools import job_tools
from typing import Dict
from utils.llm import get_completion

def analyze(state: Dict) -> Dict:
    job_posting = state.get("job_posting", "")
    manual_inputs = state.get("manual_inputs", {})
    model = state.get("model", "deepseek-ai/DeepSeek-R1")
    progress_callback = state.get("progress_callback")

    try:
        # If manual inputs are provided, use them directly
        if manual_inputs and isinstance(manual_inputs, dict) and manual_inputs.get("company_name"):
            print("Using manual inputs for job details")

            # Create a structured job details object
            job_details = {
                "extracted_details": manual_inputs,
                "requirements_analysis": job_tools[1].func(job_posting, model)
            }

            state["job_details"] = job_details
        else:
            # Otherwise, use the automatic extraction
            print("Using automatic extraction for job details")
            job_details = job_tools[0].func(job_posting, model)
            requirements_analysis = job_tools[1].func(job_posting, model)

            state["job_details"] = {
                "extracted_details": job_details,
                "requirements_analysis": requirements_analysis
            }

        # Call the progress callback if provided
        if progress_callback:
            # Format requirements for display
            tech_skills = state["job_details"].get("requirements_analysis", {}).get("technical_skills", [])
            skills_summary = f"Found {len(tech_skills)} required technical skills"
            progress_callback("job", 25, skills_summary)

    except Exception as e:
        state["error"] = f"Job analysis failed: {str(e)}"

    return state