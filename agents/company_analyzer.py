from tools.company_tools import company_tools
from typing import Dict
from utils.llm import get_completion

def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state

    model = state.get("model", "deepseek-ai/DeepSeek-R1")

    try:
        # First try to get company name from manual inputs
        manual_inputs = state.get("manual_inputs", {})
        if manual_inputs and isinstance(manual_inputs, dict):
            company_name = manual_inputs.get("company_name")
            if company_name:
                print(f"Using manual company name: {company_name}")
        else:
            # Otherwise get it from extracted details
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

        if not company_name:
            print(f"Debug - extracted_details: {extracted_details}")
            raise ValueError("Company name could not be extracted from job details")

        print(f"Analyzing company: {company_name}")
        stability_analysis = company_tools[0].func(company_name, model)
        company_reviews = company_tools[1].func(company_name, model)

        state["company_analysis"] = {
            "stability_analysis": stability_analysis,
            "company_reviews": company_reviews
        }
    except Exception as e:
        state["error"] = f"Company analysis failed: {str(e)}"
        print(f"Company analysis error: {str(e)}")

    return state