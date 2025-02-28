from tools.job_tools import job_tools
from typing import Dict

def analyze(state: Dict) -> Dict:
    job_posting = state.get("job_posting", "")
    manual_inputs = state.get("manual_inputs", {})

    try:
        # If manual inputs are provided, use them directly
        if manual_inputs and isinstance(manual_inputs, dict) and manual_inputs.get("company_name"):
            print("Using manual inputs for job details")

            # Create a structured job details object
            job_details = {
                "extracted_details": manual_inputs,
                "requirements_analysis": job_tools[1].func(job_posting)
            }

            state["job_details"] = job_details
        else:
            # Otherwise, use the automatic extraction
            print("Using automatic extraction for job details")
            job_details = job_tools[0].func(job_posting)
            requirements_analysis = job_tools[1].func(job_posting)

            state["job_details"] = {
                "extracted_details": job_details,
                "requirements_analysis": requirements_analysis
            }
    except Exception as e:
        state["error"] = f"Job analysis failed: {str(e)}"

    return state