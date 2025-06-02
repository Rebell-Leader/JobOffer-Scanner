from langchain.tools import Tool
from utils.llm import get_completion
from utils.cache import cache
import requests
from bs4 import BeautifulSoup
import json
import re
import logging

def estimate_salary_range(job_title, location, experience_level, model="deepseek-ai/DeepSeek-R1-0528"):
    print(f"Estimating salary for - Title: {job_title}, Location: {location}, Experience: {experience_level}")

    cache_key = f"salary_{job_title}_{location}_{experience_level}_{model}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

    # Step 1: Get data from comprehensive.io (simulated)
    comprehensive_data = get_comprehensive_salary_data(job_title, location, experience_level)

    # Step 2: Get cost of living data
    cost_of_living = get_cost_of_living_data(location)

    # Step 3: Use LLM to analyze and provide insights
    prompt = f"""
    Estimate salary range for:
    Job Title: {job_title}
    Location: {location}
    Experience Level: {experience_level}

    Comprehensive.io Salary Data:
    {comprehensive_data}

    Cost of Living Information:
    {cost_of_living}

    Based on this data, provide:
    1. Estimated salary range in USD and local currency (if applicable)
    2. How this compares to industry averages
    3. Explanation of the factors affecting this salary range
    4. Cost of living adjusted assessment
    5. Recommendations for salary negotiation

    Format as a well-structured markdown report with clear section headings.
    """
    try:
        response = get_completion(prompt, model)
        print(f"Salary range response length: {len(response)}")
        cache.set(cache_key, response)
        return response
    except Exception as e:
        print(f"Error in salary range estimation: {str(e)}")
        # Provide a fallback response
        return f"""
        ## Salary Analysis for {job_title} in {location}

        Based on the provided information, we estimate the following salary range:

        ### Estimated Salary Range
        - **Range:** $70,000 - $120,000 USD annually (approximate)
        - This is a standard range for this type of position and location

        ### Factors Affecting Salary
        - Experience level: {experience_level if experience_level else "Not specified"}
        - Location: {location}
        - Industry demand for the role

        ### Negotiation Recommendations
        - Research local market rates
        - Highlight specialized skills
        - Consider the total compensation package including benefits
        """

def get_comprehensive_salary_data(job_title, location, experience_level):
    """Get salary data from comprehensive.io or similar sources."""
    try:
        # Convert experience level to approximate years
        years_experience = parse_experience_level(experience_level)
        level = map_experience_to_level(years_experience)

        # Simulating data from comprehensive.io
        simulated_data = {
            "job_title": job_title,
            "location": location,
            "level": level,
            "salary_range": {
                "low": get_simulated_salary(job_title, location, years_experience, "low"),
                "median": get_simulated_salary(job_title, location, years_experience, "median"),
                "high": get_simulated_salary(job_title, location, years_experience, "high"),
            },
            "total_compensation": {
                "low": get_simulated_salary(job_title, location, years_experience, "low") * 1.2,
                "median": get_simulated_salary(job_title, location, years_experience, "median") * 1.3,
                "high": get_simulated_salary(job_title, location, years_experience, "high") * 1.4,
            }
        }

        return json.dumps(simulated_data, indent=2)

    except Exception as e:
        logging.error(f"Error getting comprehensive salary data: {str(e)}")
        return f"Unable to retrieve detailed salary data due to an error. Proceeding with approximations."

def get_cost_of_living_data(location):
    """Get cost of living data from Numbeo or similar sources."""
    try:
        # Extract city and country
        parts = location.split(',')
        city = parts[0].strip() if parts else location

        # Simulating data from Numbeo
        cost_index = get_simulated_cost_index(city)
        rent_index = get_simulated_rent_index(city)

        simulated_data = {
            "city": city,
            "cost_of_living_index": cost_index,
            "rent_index": rent_index,
            "groceries_index": cost_index * 0.8,
            "restaurant_index": cost_index * 1.1,
            "local_purchasing_power": 100 - (cost_index * 0.5),
            "comparison": {
                "new_york": f"{cost_index/100:.2f}x New York prices",
                "london": f"{cost_index/80:.2f}x London prices",
                "global_average": f"{cost_index/60:.2f}x global average"
            },
            "sample_costs": {
                "meal_inexpensive_restaurant": 10 * (cost_index/60),
                "meal_midrange_restaurant_2people": 50 * (cost_index/60),
                "monthly_rent_1bedroom_city_center": 800 * (rent_index/60),
                "monthly_rent_3bedroom_city_center": 1500 * (rent_index/60),
                "utilities_monthly": 150 * (cost_index/60),
                "internet_monthly": 60 * (cost_index/80),
            }
        }

        return json.dumps(simulated_data, indent=2)

    except Exception as e:
        logging.error(f"Error getting cost of living data: {str(e)}")
        return f"Unable to retrieve cost of living data for {location} due to an error. Proceeding with approximations."

def analyze_compensation_package(salary_details, model="deepseek-ai/DeepSeek-R1-0528"):
    """Analyze the full compensation package including benefits."""
    prompt = f"""
    Analyze the full compensation package including:
    - Base salary
    - Bonuses
    - Stock options
    - Benefits
    - Retirement plans
    - Health insurance
    - Other perks

    Package details:
    {salary_details}

    Provide:
    1. Assessment of the total monetary value
    2. Comparison to industry standards
    3. Strengths of this package
    4. Weaknesses or missing components
    5. Recommendations for negotiation

    Format as a well-structured markdown report with clear section headings.
    """
    try:
        response = get_completion(prompt, model)
        return response
    except Exception as e:
        print(f"Error in compensation package analysis: {str(e)}")
        return f"## Compensation Package Analysis\n\nUnable to complete detailed analysis due to a technical issue."

# Helper functions

def parse_experience_level(experience_level):
    """Extract years from experience level text."""
    if not experience_level:
        return 3  # Default to mid-level experience

    # Look for patterns like "3+ years", "5 years", etc.
    years_pattern = r'(\d+)(?:\+)?\s*(?:year|yr)'
    match = re.search(years_pattern, experience_level.lower())

    if match:
        return int(match.group(1))

    # Handle text descriptions
    if 'entry' in experience_level.lower() or 'junior' in experience_level.lower():
        return 0
    elif 'mid' in experience_level.lower():
        return 3
    elif 'senior' in experience_level.lower():
        return 5
    elif 'lead' in experience_level.lower() or 'manager' in experience_level.lower():
        return 7
    elif 'director' in experience_level.lower():
        return 10
    elif 'executive' in experience_level.lower() or 'vp' in experience_level.lower():
        return 15

    # Default value
    return 3

def map_experience_to_level(years):
    """Map years of experience to job level."""
    if years < 1:
        return "IC1"
    elif years < 3:
        return "IC2"
    elif years < 6:
        return "IC3"
    elif years < 9:
        return "IC4"
    elif years < 12:
        return "IC5"
    else:
        return "IC6+"

def get_simulated_salary(job_title, location, years_experience, percentile):
    """Generate simulated salary based on job details."""
    # Base values for different roles
    base_salaries = {
        "software engineer": 80000,
        "data scientist": 85000,
        "product manager": 90000,
        "designer": 70000,
        "marketing": 65000,
        "sales": 60000,
        "analyst": 65000,
        "manager": 100000,
        "director": 130000,
        "engineer": 75000,
        "developer": 80000,
        "ml": 90000,
        "ai": 95000,
    }

    # Find the closest matching job title
    base = 70000  # Default
    for title, salary in base_salaries.items():
        if title in job_title.lower():
            base = salary
            break

    # Location multipliers
    location_multipliers = {
        "san francisco": 1.5,
        "new york": 1.4,
        "seattle": 1.3,
        "boston": 1.25,
        "los angeles": 1.3,
        "chicago": 1.2,
        "austin": 1.15,
        "remote": 1.0,
        "london": 1.2,
        "berlin": 0.9,
        "paris": 0.9,
        "toronto": 0.85,
        "sydney": 0.95,
        "singapore": 1.1,
        "tokyo": 1.0,
        "zurich": 1.4,
        "prague": 0.7,
    }

    # Find location multiplier
    location_mult = 1.0  # Default
    for loc, mult in location_multipliers.items():
        if location and loc in location.lower():
            location_mult = mult
            break

    # Experience multiplier
    exp_mult = 1.0 + (years_experience * 0.06)

    # Percentile adjustments
    percentile_mults = {
        "low": 0.8,
        "median": 1.0,
        "high": 1.2
    }

    # Calculate final salary
    final_salary = base * location_mult * exp_mult * percentile_mults[percentile]
    return round(final_salary, -3)  # Round to nearest thousand

def get_simulated_cost_index(city):
    """Generate simulated cost of living index for a city."""
    city_indices = {
        "new york": 100,
        "san francisco": 95,
        "london": 83,
        "tokyo": 86,
        "paris": 80,
        "berlin": 65,
        "singapore": 83,
        "sydney": 80,
        "toronto": 73,
        "chicago": 70,
        "seattle": 85,
        "austin": 65,
        "boston": 82,
        "los angeles": 77,
        "zurich": 123,
        "geneva": 108,
        "dublin": 75,
        "prague": 50,
    }

    # Find matching city
    for known_city, index in city_indices.items():
        if city and known_city in city.lower():
            return index

    # Default is medium cost
    return 65

def get_simulated_rent_index(city):
    """Generate simulated rent index for a city."""
    # Rent is typically higher variance than general cost of living
    city_indices = {
        "new york": 100,
        "san francisco": 108,
        "london": 87,
        "tokyo": 60,
        "paris": 70,
        "berlin": 50,
        "singapore": 78,
        "sydney": 75,
        "toronto": 68,
        "chicago": 60,
        "seattle": 80,
        "austin": 55,
        "boston": 78,
        "los angeles": 85,
        "zurich": 90,
        "geneva": 85,
        "dublin": 80,
        "prague": 40,
    }

    # Find matching city
    for known_city, index in city_indices.items():
        if city and known_city in city.lower():
            return index

    # Default is medium cost
    return 50

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