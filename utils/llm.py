from openai import OpenAI
from typing import Dict, Optional
import os

# Initialize OpenAI client with Featherless API
client = OpenAI(
    base_url="https://api.featherless.ai/v1",
    api_key=os.getenv("FEATHERLESS_API_KEY")  # Get from environment variable
)

def get_llm_client() -> OpenAI:
    """Get the OpenAI client instance."""
    return client

def get_completion(prompt: str, model: str = "deepseek-ai/DeepSeek-R1-0528") -> str:
    """Get completion from the LLM."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant specialized in job and company analysis."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7,
        )
        # Extract the text content from the response
        content = response.choices[0].message.content
        return content or ""
    except Exception as e:
        print(f"Error getting LLM completion: {str(e)}")
        # Return an error message that explains the issue
        return f"## Analysis Error\n\nUnable to complete analysis due to API connectivity issue. Please verify your API configuration."