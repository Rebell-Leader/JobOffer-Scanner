
import os
from typing import Dict, List

def check_environment_setup() -> Dict[str, bool]:
    """
    Check if environment variables and configuration are properly set.
    Returns status of different components.
    """
    status = {
        "openai_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "database_url": bool(os.getenv("DATABASE_URL")),
        "demo_mode": not bool(os.getenv("OPENAI_API_KEY")),  # True if no API key
    }
    return status

def get_missing_configs() -> List[str]:
    """Get list of missing configuration items for production."""
    missing = []
    
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY - Required for LLM functionality")
    
    # Future API integrations
    future_apis = [
        "ALPHA_VANTAGE_API_KEY - For company financial data",
        "GLASSDOOR_API_KEY - For salary and company reviews", 
        "NUMBEO_API_KEY - For cost of living data",
        "NEWS_API_KEY - For company news and layoff data"
    ]
    missing.extend(future_apis)
    
    return missing

def print_environment_status():
    """Print current environment configuration status."""
    print("\n" + "="*50)
    print("AI JOB ANALYSIS PLATFORM - ENVIRONMENT STATUS")
    print("="*50)
    
    status = check_environment_setup()
    
    if status["demo_mode"]:
        print("🔄 DEMO MODE: Using simulated data and mock responses")
        print("📝 To enable real API calls, add API keys to environment")
    else:
        print("🚀 PRODUCTION MODE: Using real API endpoints")
    
    print(f"\n📊 Configuration Status:")
    for key, value in status.items():
        symbol = "✅" if value else "❌"
        print(f"  {symbol} {key.replace('_', ' ').title()}")
    
    missing = get_missing_configs()
    if missing:
        print(f"\n📋 Missing Configurations for Full Production:")
        for item in missing:
            print(f"  • {item}")
    
    print("="*50 + "\n")
