from tools.job_tools import job_tools
from typing import Dict

def analyze(state: Dict) -> Dict:
    job_posting = state.get("job_posting", "")
    
    try:
        job_details = job_tools[0].func(job_posting)
        requirements_analysis = job_tools[1].func(job_posting)
        
        state["job_details"] = {
            "extracted_details": job_details,
            "requirements_analysis": requirements_analysis
        }
    except Exception as e:
        state["error"] = f"Job analysis failed: {str(e)}"
    
    return state
