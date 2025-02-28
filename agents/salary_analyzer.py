from tools.salary_tools import salary_tools
from typing import Dict

def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state
        
    try:
        job_details = state.get("job_details", {})
        extracted_details = job_details.get("extracted_details", {})
        
        salary_range = salary_tools[0].func(
            job_title=extracted_details.get("Job Title", ""),
            location=extracted_details.get("Location", ""),
            experience_level=extracted_details.get("Experience Level", "")
        )
        
        state["salary_analysis"] = {
            "estimated_range": salary_range
        }
    except Exception as e:
        state["error"] = f"Salary analysis failed: {str(e)}"
    
    return state
