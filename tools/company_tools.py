from langchain.tools import Tool
from utils.llm import get_completion
from utils.cache import cache
import requests
from bs4 import BeautifulSoup
import json
import re
import logging

def analyze_company_stability(company_name: str, model: str = "deepseek-ai/DeepSeek-R1") -> str:
    """Analyze company stability based on news, layoffs and market data."""
    cache_key = f"stability_{company_name}_{model}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

    # Step 1: Check for layoffs data
    layoffs_info = check_layoffs_data(company_name)

    # Step 2: Get company news
    news_summary = get_company_news(company_name)

    # Step 3: Use LLM to synthesize all information
    prompt = f"""
    Analyze the stability and growth prospects for {company_name}.

    Layoff Information:
    {layoffs_info}

    Recent News:
    {news_summary}

    Based on this information, provide:
    1. An analysis of the company's current stability
    2. Market position assessment
    3. Growth trajectory outlook
    4. Risk factors for job seekers
    5. Overall stability score (1-10, with 10 being extremely stable)

    Format as a well-structured markdown report.
    """
    response = get_completion(prompt, model)
    cache.set(cache_key, response)
    return response

def check_layoffs_data(company_name: str) -> str:
    """Check layoffs.fyi for information about company layoffs."""
    try:
        # Simplified approach - in production would use more robust techniques
        url = "https://layoffs.fyi/"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract table data - note this is a simplified version
        # would need to be adapted based on the actual site structure
        table_data = []
        tables = soup.find_all('table')

        company_found = False
        layoff_info = ""

        # Search for the company name in the page content
        page_text = soup.get_text().lower()
        if company_name.lower() in page_text:
            company_found = True
            # Find the nearest date and count information
            # This is a simplified approach
            paragraphs = soup.find_all('p')
            for p in paragraphs:
                if company_name.lower() in p.get_text().lower():
                    layoff_info += p.get_text() + "\n"

        if company_found:
            return f"Found layoff information for {company_name}:\n{layoff_info}"
        else:
            return f"No recent layoffs found for {company_name} in the layoffs.fyi database."

    except Exception as e:
        logging.error(f"Error checking layoffs data: {str(e)}")
        return f"Unable to retrieve layoff data due to an error: {str(e)}"

def get_company_news(company_name: str) -> str:
    """Get and summarize recent news about the company."""
    try:
        # Using a general search approach
        # In production would use a proper news API
        search_term = f"{company_name} company news"
        url = f"https://www.google.com/search?q={search_term.replace(' ', '+')}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract news headlines and snippets
        news_results = []

        # Extract from search results
        results = soup.find_all('div', class_='g')
        for result in results[:5]:  # Take top 5 results
            title_element = result.find('h3')
            if title_element:
                title = title_element.text
                snippet_element = result.find('div', class_='IsZvec')
                snippet = snippet_element.text if snippet_element else "No snippet available"
                news_results.append(f"Title: {title}\nSnippet: {snippet}\n")

        if news_results:
            return "\n".join(news_results)
        else:
            return f"No recent news found for {company_name}."

    except Exception as e:
        logging.error(f"Error getting company news: {str(e)}")
        return f"Unable to retrieve news data due to an error: {str(e)}"

def get_company_reviews(company_name: str, model: str = "deepseek-ai/DeepSeek-R1") -> str:
    """Analyze employee reviews for the company."""
    try:
        # Simulating Glassdoor search
        # In production would use an API or more robust scraping
        search_term = f"{company_name} reviews glassdoor"
        url = f"https://www.google.com/search?q={search_term.replace(' ', '+')}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract review information
        reviews_text = soup.get_text()

        # Use LLM to analyze and summarize review information
        prompt = f"""
        Based on the following search results about {company_name} reviews from Glassdoor:

        {reviews_text[:2000]}  # Limiting text length

        Provide an analysis of:
        1. Overall employee satisfaction
        2. Work-life balance
        3. Career growth opportunities
        4. Management effectiveness
        5. Company culture

        Make reasonable inferences based on the available information.
        Format as a well-structured markdown report.
        """

        response = get_completion(prompt, model)
        return response

    except Exception as e:
        logging.error(f"Error getting company reviews: {str(e)}")
        return f"Unable to retrieve company reviews due to an error: {str(e)}"

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