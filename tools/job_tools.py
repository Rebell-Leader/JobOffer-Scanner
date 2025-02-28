from langchain.tools import Tool
from utils.llm import get_completion
from utils.cache import cache

def extract_job_details(job_posting: str) -> dict:
    """Extract key details from a job posting."""
    cache_key = f"job_details_{hash(job_posting)}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    prompt = f"""
    Extract key details from this job posting:
    {job_posting}
    
    Provide details in these categories:
    - Job Title
    - Company Name
    - Location
    - Experience Level
    - Required Skills
    - Preferred Skills
    - Job Type (Full-time/Part-time/Contract)
    """
    response = get_completion(prompt)
    cache.set(cache_key, response)
    return response

def analyze_requirements(job_posting: str) -> str:
    """Analyze job requirements and provide insights."""
    prompt = f"""
    Analyze the requirements and qualifications in this job posting:
    {job_posting}
    
    Provide insights on:
    1. Key technical skills required
    2. Soft skills emphasized
    3. Education requirements
    4. Experience level needed
    5. Any unique or standout requirements
    """
    response = get_completion(prompt)
    return response

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
