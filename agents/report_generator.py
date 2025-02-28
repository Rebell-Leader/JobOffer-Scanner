from utils.llm import get_completion
from typing import Dict

def generate(state: Dict) -> Dict:
    if state.get("error"):
        return state
        
    try:
        prompt = f"""
        Generate a comprehensive job analysis report based on:
        
        Job Details:
        {state.get('job_details', {})}
        
        Company Analysis:
        {state.get('company_analysis', {})}
        
        Salary Analysis:
        {state.get('salary_analysis', {})}
        
        Format the report with clear sections and recommendations.
        Include a final recommendation (Recommended/Consider/Not Recommended).
        """
        
        report = get_completion(prompt)
        state["final_report"] = report
    except Exception as e:
        state["error"] = f"Report generation failed: {str(e)}"
    
    return state
