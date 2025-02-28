from tools.company_tools import company_tools
from typing import Dict

def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state

    try:
        job_details = state.get("job_details", {})
        extracted_details = job_details.get("extracted_details", {})

        if not isinstance(extracted_details, dict):
            raise ValueError("Job details not in expected format")

        company_name = extracted_details.get("company_name", "")
        if not company_name or company_name == "Unknown":
            raise ValueError("Company name not found in job details")

        stability_analysis = company_tools[0].func(company_name)
        company_reviews = company_tools[1].func(company_name)

        state["company_analysis"] = {
            "stability_analysis": stability_analysis,
            "company_reviews": company_reviews
        }
    except Exception as e:
        state["error"] = f"Company analysis failed: {str(e)}"

    return state