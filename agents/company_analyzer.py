from tools.company_tools import company_tools
from typing import Dict

def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state

    try:
        job_details = state.get("job_details", {})
        extracted_details = job_details.get("extracted_details", {})

        # More flexible company name extraction
        company_name = None

        # Try different possible keys/formats
        if isinstance(extracted_details, dict):
            company_name = (
                extracted_details.get("company_name") or
                extracted_details.get("Company Name") or
                extracted_details.get("company") or
                None
            )

        # If still no company name, try to extract from raw job details
        if not company_name and isinstance(job_details, dict):
            company_name = job_details.get("company_name")

        if not company_name:
            print(f"Debug - extracted_details: {extracted_details}")
            raise ValueError("Company name could not be extracted from job details")

        stability_analysis = company_tools[0].func(company_name)
        company_reviews = company_tools[1].func(company_name)

        state["company_analysis"] = {
            "stability_analysis": stability_analysis,
            "company_reviews": company_reviews
        }
    except Exception as e:
        state["error"] = f"Company analysis failed: {str(e)}"
        print(f"Company analysis error - Details: {job_details}")  # Debug logging

    return state