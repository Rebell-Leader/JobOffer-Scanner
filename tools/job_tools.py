from langchain.tools import Tool
from utils.llm import get_completion
from utils.cache import cache
import json

def extract_job_details(job_posting: str) -> dict:
    """Extract key details from a job posting."""
    cache_key = f"job_details_{hash(job_posting)}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

    prompt = f"""
    Carefully analyze this job posting and extract key information in JSON format. 
    Pay special attention to identifying the company name, which might be mentioned at the start 
    or within the job description.

    Job posting:
    {job_posting}

    Return a JSON object with these fields:
    {{
        "company_name": "The exact company name as mentioned in the text",
        "job_title": "The main job title/role",
        "location": "Full location details",
        "experience_level": "Required years/level of experience",
        "required_skills": ["List of main required technical skills"],
        "preferred_skills": ["List of preferred/nice-to-have skills"],
        "compensation": "Any mentioned salary or compensation details",
        "job_type": "Full-time/Part-time/Contract",
        "responsibilities": ["Key job responsibilities"],
        "company_description": "Brief description of the company"
    }}

    Be very precise and ensure no key information is missed.
    """
    try:
        response = get_completion(prompt)
        if isinstance(response, str):
            parsed_response = json.loads(response)
        else:
            parsed_response = response
        cache.set(cache_key, parsed_response)
        return parsed_response
    except json.JSONDecodeError:
        return {
            "company_name": "Unknown",
            "job_title": "Unknown",
            "location": "Unknown",
            "experience_level": "Not specified",
            "required_skills": [],
            "preferred_skills": [],
            "compensation": "Not specified",
            "job_type": "Not specified",
            "responsibilities": [],
            "company_description": "Not specified"
        }

def analyze_requirements(job_posting: str) -> dict:
    """Analyze job requirements and provide insights."""
    prompt = f"""
    Analyze the requirements and qualifications in this job posting:
    {job_posting}

    Return a JSON object with:
    {{
        "technical_skills": ["List of required technical skills with proficiency levels"],
        "soft_skills": ["List of emphasized soft skills"],
        "education": "Detailed education requirements",
        "experience": "Detailed experience requirements including years and specific domains",
        "unique_requirements": ["Any standout or unusual requirements"],
        "tools_and_technologies": ["Specific tools, frameworks, or technologies mentioned"]
    }}
    """
    try:
        response = get_completion(prompt)
        if isinstance(response, str):
            return json.loads(response)
        return response
    except json.JSONDecodeError:
        return {
            "technical_skills": [],
            "soft_skills": [],
            "education": "Not specified",
            "experience": "Not specified",
            "unique_requirements": [],
            "tools_and_technologies": []
        }

job_tools = [
    Tool(
        name="extract_job_details",
        func=extract_job_details,
        description="Extracts key details from a job posting"
    ),
    Tool(
        name="analyze_requirements",
        func=analyze_requirements,
        description="Analyzes job requirements and provides insights"
    )
]