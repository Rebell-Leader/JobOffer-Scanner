from openai import OpenAI
from typing import Dict, Optional

# Initialize OpenAI client with Featherless API
client = OpenAI(
    base_url="https://api.featherless.ai/v1",
    api_key=None  # Will be set via environment variable
)

def get_llm_client() -> OpenAI:
    """Get the OpenAI client instance."""
    return client

def get_completion(prompt: str) -> Dict:
    """Get completion from the LLM."""
    try:
        response = client.chat.completions.create(
            model='deepseek-ai/DeepSeek-R1',
            messages=[
                {"role": "system", "content": "You are a helpful assistant specialized in job and company analysis."},
                {"role": "user", "content": prompt}
            ],
        )
        return response.model_dump()['choices'][0]['message']['content']
    except Exception as e:
        print(f"Error getting LLM completion: {str(e)}")
        return {"error": str(e)}
