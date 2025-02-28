from tools.company_tools import company_tools
from typing import Dict

def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state
        
    try:
        job_details = state.get("job_details", {})
        company_name = job_details.get("extracted_details", {}).get("Company Name", "")
        
        stability_analysis = company_tools[0].func(company_name)
        company_reviews = company_tools[1].func(company_name)
        
        state["company_analysis"] = {
            "stability_analysis": stability_analysis,
            "company_reviews": company_reviews
        }
    except Exception as e:
        state["error"] = f"Company analysis failed: {str(e)}"
    
    return state
