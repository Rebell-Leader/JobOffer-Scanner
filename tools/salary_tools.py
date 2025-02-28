from langchain.tools import Tool
from utils.llm import get_completion
from utils.cache import cache

def estimate_salary_range(job_title, location, experience_level):
    cache_key = f"salary_{job_title}_{location}_{experience_level}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    prompt = f"""
    Estimate salary range for:
    Job Title: {job_title}
    Location: {location}
    Experience Level: {experience_level}
    
    Provide range and explanation based on market data.
    """
    response = get_completion(prompt)
    cache.set(cache_key, response)
    return response

def analyze_compensation_package(salary_details):
    prompt = f"""
    Analyze the full compensation package including:
    - Base salary
    - Bonuses
    - Stock options
    - Benefits
    
    Package details:
    {salary_details}
    """
    response = get_completion(prompt)
    return response

salary_tools = [
    Tool(
        name="estimate_salary_range",
        func=estimate_salary_range,
        description="Estimates salary range for a given job"
    ),
    Tool(
        name="analyze_compensation_package",
        func=analyze_compensation_package,
        description="Analyzes full compensation package"
    )
]
