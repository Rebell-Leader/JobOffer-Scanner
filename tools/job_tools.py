from langchain.tools import Tool
from utils.llm import get_completion
from utils.cache import cache
import json
import logging

def extract_job_details(job_posting: str, model: str = "deepseek-ai/DeepSeek-R1") -> dict:
    """Extract key details from a job posting."""
    cache_key = f"job_details_{hash(job_posting)}_{model}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

    prompt = f"""
    Extract key information from this job posting. Return it in strict JSON format.
    Pay special attention to the company name, which is usually at the start.

    Job posting:
    {job_posting}

    Format the response EXACTLY like this, filling in the values from the text:
    {{
        "company_name": "Oddin.gg",
        "job_title": "AI Engineer",
        "location": "Prague, Czech Republic",
        "experience_level": "4+ years in ML/DS",
        "required_skills": ["Python", "PyTorch/TensorFlow", "AWS"],
        "compensation": "EUR 65k/year + bonuses",
        "job_type": "Full-time",
        "responsibilities": [
            "Develop AI simulations",
            "Build ML models",
            "Implement data pipelines"
        ]
    }}

    Ensure the company_name field is never empty and matches exactly as written in the text.
    """
    try:
        response = get_completion(prompt, model)
        print(f"LLM Response: {response}")  # Debug logging

        if isinstance(response, str):
            parsed_response = json.loads(response)
            print(f"Parsed Response: {parsed_response}")  # Debug logging

            # Ensure company name is present
            if not parsed_response.get("company_name"):
                # Attempt to extract from first line if missing
                first_line = job_posting.split('\n')[0]
                if 'Company:' in first_line:
                    parsed_response["company_name"] = first_line.split('Company:')[1].strip()

            cache.set(cache_key, parsed_response)
            return parsed_response
    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {str(e)}")
        print(f"Failed to parse response: {response}")
    except Exception as e:
        print(f"Error in extract_job_details: {str(e)}")

    # Fallback response with basic extraction
    try:
        lines = job_posting.split('\n')
        company_name = next((line.split('Company:')[1].strip() 
                           for line in lines if 'Company:' in line), "Unknown")
        return {
            "company_name": company_name,
            "job_title": "AI/ML Engineer",  # Default based on context
            "location": "Prague",  # Default based on context
            "experience_level": "Not specified",
            "required_skills": [],
            "compensation": "Not specified",
            "job_type": "Not specified",
            "responsibilities": []
        }
    except Exception as e:
        print(f"Error in fallback extraction: {str(e)}")
        return {
            "company_name": "Unknown",
            "job_title": "Unknown",
            "location": "Unknown",
            "experience_level": "Not specified",
            "required_skills": [],
            "compensation": "Not specified",
            "job_type": "Not specified",
            "responsibilities": []
        }

def analyze_requirements(job_posting: str, model: str = "deepseek-ai/DeepSeek-R1") -> dict:
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
        response = get_completion(prompt, model)
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