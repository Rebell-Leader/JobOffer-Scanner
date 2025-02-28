from langchain.tools import Tool
from utils.llm import get_completion
from utils.cache import cache

def analyze_company_stability(company_name: str) -> str:
    """Analyze company stability based on available information."""
    cache_key = f"stability_{company_name}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    prompt = f"""
    Analyze the stability and growth prospects for {company_name}.
    Consider:
    1. Recent news and developments
    2. Market position
    3. Industry trends
    4. Growth trajectory
    5. Financial health indicators (if public)
    """
    response = get_completion(prompt)
    cache.set(cache_key, response)
    return response

def get_company_reviews(company_name: str) -> str:
    """Get and analyze company reviews and ratings."""
    prompt = f"""
    Analyze employee reviews and workplace culture for {company_name}.
    Consider:
    1. Overall employee satisfaction
    2. Work-life balance
    3. Career growth opportunities
    4. Management effectiveness
    5. Company culture
    """
    response = get_completion(prompt)
    return response

company_tools = [
    Tool(
        name="analyze_company_stability",
        func=analyze_company_stability,
        description="Analyzes company stability and growth prospects"
    ),
    Tool(
        name="get_company_reviews",
        func=get_company_reviews,
        description="Gets and analyzes company reviews and ratings"
    )
]
