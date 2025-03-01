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
        print(f"LLM job_details Response: {response}")  # Debug logging

        # Clean up response if there are markdown code blocks
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].split("```")[0].strip()

        if isinstance(response, str):
            # Remove any leading/trailing whitespace and handle potential markdown
            clean_response = response.strip()
            try:
                parsed_response = json.loads(clean_response)
                print(f"Parsed Job Details Response: {parsed_response}")  # Debug logging

                # Ensure company name is present
                if not parsed_response.get("company_name"):
                    # Attempt to extract from first line if missing
                    first_line = job_posting.split('\n')[0]
                    if 'Company:' in first_line:
                        parsed_response["company_name"] = first_line.split('Company:')[1].strip()

                # Ensure required_skills is a list
                if "required_skills" in parsed_response and not isinstance(parsed_response["required_skills"], list):
                    if isinstance(parsed_response["required_skills"], str):
                        parsed_response["required_skills"] = [skill.strip() for skill in parsed_response["required_skills"].split(",")]
                    else:
                        parsed_response["required_skills"] = []

                # Ensure responsibilities is a list
                if "responsibilities" in parsed_response and not isinstance(parsed_response["responsibilities"], list):
                    if isinstance(parsed_response["responsibilities"], str):
                        parsed_response["responsibilities"] = [resp.strip() for resp in parsed_response["responsibilities"].split(",")]
                    else:
                        parsed_response["responsibilities"] = []

                cache.set(cache_key, parsed_response)
                return parsed_response
            except json.JSONDecodeError as e:
                print(f"JSON parsing error: {str(e)}")
                print(f"Failed to parse response: {clean_response}")
        else:
            print(f"Unexpected response type: {type(response)}")
    except Exception as e:
        print(f"Error in extract_job_details: {str(e)}")

    # Fallback response with basic extraction
    try:
        lines = job_posting.split('\n')
        company_name = "Unknown"
        job_title = "Unknown"
        location = "Unknown"

        # Try to extract company name
        for line in lines:
            if 'Company:' in line:
                company_name = line.split('Company:')[1].strip()
                break

        # Try to extract job title
        for line in lines:
            if 'Title:' in line or 'Position:' in line:
                job_title = line.split(':')[1].strip()
                break

        # Try to extract location
        for line in lines:
            if 'Location:' in line:
                location = line.split('Location:')[1].strip()
                break

        fallback_result = {
            "company_name": company_name,
            "job_title": job_title or "AI/ML Engineer",  # Default based on context
            "location": location or "Remote",  # Default based on context
            "experience_level": "Not specified",
            "required_skills": [],
            "compensation": "Not specified",
            "job_type": "Full-time",
            "responsibilities": []
        }
        print(f"Using fallback job details: {fallback_result}")
        return fallback_result
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
    cache_key = f"requirements_{hash(job_posting)}_{model}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

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

    Return ONLY the JSON without any explanation text. The JSON should be properly formatted.
    """
    try:
        response = get_completion(prompt, model)
        print(f"LLM requirements Response: {response}")  # Debug logging

        # Clean up response if there are markdown code blocks
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].split("```")[0].strip()

        if isinstance(response, str):
            # Remove any leading/trailing whitespace
            clean_response = response.strip()
            try:
                requirements = json.loads(clean_response)

                # Ensure all fields are properly formatted as lists
                for key in ["technical_skills", "soft_skills", "unique_requirements", "tools_and_technologies"]:
                    if key in requirements and not isinstance(requirements[key], list):
                        if isinstance(requirements[key], str):
                            requirements[key] = [item.strip() for item in requirements[key].split(",")]
                        else:
                            requirements[key] = []

                cache.set(cache_key, requirements)
                return requirements
            except json.JSONDecodeError as e:
                print(f"JSON parsing error in requirements: {str(e)}")
                print(f"Failed to parse requirements response: {clean_response}")
        else:
            print(f"Unexpected requirements response type: {type(response)}")
    except Exception as e:
        print(f"Error in analyze_requirements: {str(e)}")

    # Fallback requirements
    fallback = {
        "technical_skills": [],
        "soft_skills": [],
        "education": "Not specified in the extracted text",
        "experience": "Not specified in the extracted text",
        "unique_requirements": [],
        "tools_and_technologies": []
    }
    print("Using fallback requirements analysis")
    return fallback

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