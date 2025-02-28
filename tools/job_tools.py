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
    Extract key details from this job posting in JSON format:
    {job_posting}

    Return a JSON object with these fields:
    - job_title: string
    - company_name: string
    - location: string
    - experience_level: string
    - required_skills: list of strings
    - preferred_skills: list of strings
    - job_type: string (Full-time/Part-time/Contract)
    """
    try:
        response = get_completion(prompt)
        # Convert string response to dictionary
        if isinstance(response, str):
            parsed_response = json.loads(response)
        else:
            parsed_response = response
        cache.set(cache_key, parsed_response)
        return parsed_response
    except json.JSONDecodeError:
        return {
            "job_title": "Unknown",
            "company_name": "Unknown",
            "location": "Unknown",
            "experience_level": "Not specified",
            "required_skills": [],
            "preferred_skills": [],
            "job_type": "Not specified"
        }

def analyze_requirements(job_posting: str) -> dict:
    """Analyze job requirements and provide insights."""
    prompt = f"""
    Analyze the requirements and qualifications in this job posting. Return a JSON object with:
    {job_posting}

    Return a JSON object with:
    - technical_skills: list of required technical skills
    - soft_skills: list of emphasized soft skills
    - education: string describing education requirements
    - experience: string describing years/level of experience needed
    - unique_requirements: list of any standout or unusual requirements
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
            "unique_requirements": []
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